"""local_date_dirs must never under-report — the server replaces its inventory
with whatever we send, so a short list un-holds folders the operator keeps.

Run: .venv/bin/python -m pytest tools/traxdb_sync/test_local_daemon.py
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from traxdb_sync.local_daemon import local_date_dirs, safe_name  # noqa: E402


class LocalDateDirsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name in ('2026-07-15', '2026-05-28', '2026-07-13.part', '_reports'):
            os.makedirs(os.path.join(self.tmp.name, name))

    def test_lists_date_dirs_and_ignores_part_and_other_dirs(self):
        self.assertEqual(local_date_dirs(self.tmp.name), ['2026-05-28', '2026-07-15'])

    def test_empty_folder_is_still_reported(self):
        """An emptied folder means 'I want nothing from that date'. Omitting it
        would let the daemon refill it."""
        os.makedirs(os.path.join(self.tmp.name, '2026-06-01'))
        self.assertIn('2026-06-01', local_date_dirs(self.tmp.name))

    def test_retries_eintr_then_succeeds(self):
        real = os.scandir
        calls = {'n': 0}

        def flaky(path):
            calls['n'] += 1
            if calls['n'] == 1:
                raise InterruptedError(4, 'Interrupted system call')
            return real(path)

        with patch('traxdb_sync.local_daemon.os.scandir', side_effect=flaky), \
             patch('traxdb_sync.local_daemon.time.sleep'):
            self.assertEqual(local_date_dirs(self.tmp.name), ['2026-05-28', '2026-07-15'])
        self.assertEqual(calls['n'], 2)

    def test_persistent_eintr_raises_instead_of_under_reporting(self):
        with patch('traxdb_sync.local_daemon.os.scandir',
                   side_effect=InterruptedError(4, 'Interrupted system call')), \
             patch('traxdb_sync.local_daemon.time.sleep'):
            with self.assertRaises(InterruptedError):
                local_date_dirs(self.tmp.name)

    def test_missing_root_raises_rather_than_returning_empty(self):
        """Returning [] here would wipe the server's inventory and make every
        held date claimable again."""
        with self.assertRaises(FileNotFoundError):
            local_date_dirs(os.path.join(self.tmp.name, 'gone'))


class SafeNameTests(unittest.TestCase):
    def test_strips_traversal(self):
        self.assertEqual(safe_name('../2026-07-15/x.flac'), 'x.flac')

    def test_rejects_empty_and_dots(self):
        for bad in ('', '   ', '..', '.'):
            with self.assertRaises(ValueError):
                safe_name(bad)




class ConcurrentDownloadTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dest = self.root / '2026-11-02'
        self.item = {'inferred_date': '2026-11-02', 'tracks': [
            {'filename': f'{i}.flac', 'pixeldrain_file_id': str(i), 'size': 3}
            for i in range(8)]}

    def client(self, action):
        from unittest.mock import Mock
        parent = Mock()
        def clone():
            child = Mock()
            child.download_file.side_effect = action
            return child
        parent.clone.side_effect = clone
        return parent

    def test_bound_independent_clients_and_atomic_complete(self):
        import threading
        from pathlib import Path
        from traxdb_sync.local_daemon import download_one
        lock = threading.Lock()
        barrier = threading.Barrier(4)
        active = peak = 0
        def action(file_id, path, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            self.assertFalse(self.dest.exists())
            self.assertEqual(Path(path).parent.parent, self.root)
            self.assertTrue(Path(path).parent.name.endswith('.part'))
            barrier.wait(timeout=5)
            Path(path).write_bytes(b'abc')
            with lock:
                active -= 1
        client = self.client(action)
        records = download_one(client, self.item, str(self.root))
        self.assertEqual(peak, 4)
        self.assertEqual(client.clone.call_count, 8)
        self.assertEqual([r['pixeldrain_file_id'] for r in records], list(map(str, range(8))))
        self.assertTrue(all(r['bytes'] == 3 for r in records))
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [f'{i}.flac' for i in range(8)])
        self.assertEqual(list(self.root.iterdir()), [self.dest])

    def test_failure_joins_late_writer_before_cleanup(self):
        import threading
        from pathlib import Path
        from traxdb_sync.local_daemon import download_one
        started = threading.Event()
        failed = threading.Event()
        wrote = threading.Event()
        self.item['tracks'] = self.item['tracks'][:2]
        def action(file_id, path, **kwargs):
            if file_id == '0':
                self.assertTrue(started.wait(5))
                failed.set()
                raise RuntimeError('network failed')
            started.set()
            self.assertTrue(failed.wait(5))
            Path(path).write_bytes(b'abc')
            wrote.set()
        with self.assertRaisesRegex(RuntimeError, 'network failed'):
            download_one(self.client(action), self.item, str(self.root), max_workers=2)
        self.assertTrue(wrote.is_set())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_prevalidation_before_client_or_writes(self):
        from traxdb_sync.local_daemon import download_one
        for bad in ['', '..', '\x00.flac', '0.flac', '../0.flac', '0.FLAC']:
            with self.subTest(bad=bad):
                self.item['tracks'][7]['filename'] = bad
                client = self.client(lambda *a, **k: self.fail('network called'))
                with self.assertRaises((ValueError, RuntimeError)):
                    download_one(client, self.item, str(self.root))
                client.clone.assert_not_called()
                self.assertEqual(list(self.root.iterdir()), [])

    def test_traversal_reduced_to_staging_basename(self):
        from pathlib import Path
        from traxdb_sync.local_daemon import download_one
        self.item['tracks'] = [dict(self.item['tracks'][0], filename='../2026-08-01/x.flac')]
        records = download_one(self.client(lambda _, p, **kw: Path(p).write_bytes(b'abc')),
                               self.item, str(self.root))
        self.assertEqual(records[0]['local_path'], str(self.dest / 'x.flac'))
        self.assertEqual(list(self.root.iterdir()), [self.dest])

    def test_existing_empty_folder_and_symlink_untouched(self):
        from traxdb_sync.local_daemon import download_one
        self.dest.mkdir()
        client = self.client(lambda *a, **kw: self.fail('network'))
        with self.assertRaisesRegex(RuntimeError, 'existing folder'):
            download_one(client, self.item, str(self.root))
        self.assertEqual(list(self.dest.iterdir()), [])
        self.dest.rmdir()
        self.dest.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(RuntimeError, 'existing folder'):
            download_one(client, self.item, str(self.root))
        client.clone.assert_not_called()
        self.assertTrue(self.dest.is_symlink())

    def test_destination_appearing_during_download_untouched(self):
        from pathlib import Path
        from traxdb_sync.local_daemon import download_one
        self.item['tracks'] = self.item['tracks'][:1]
        def action(_, path, **kw):
            self.dest.mkdir()
            Path(path).write_bytes(b'abc')
        with self.assertRaisesRegex(RuntimeError, 'existing folder'):
            download_one(self.client(action), self.item, str(self.root))
        self.assertEqual(list(self.root.iterdir()), [self.dest])
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_size_mismatch_never_published_and_previous_partial_preserved(self):
        from pathlib import Path
        from traxdb_sync.local_daemon import download_one
        partial = self.root / '2026-11-02.part'
        partial.mkdir()
        (partial / 'live.flac').write_bytes(b'keep')
        with self.assertRaisesRegex(RuntimeError, 'size mismatch'):
            download_one(self.client(lambda _, p, **kw: Path(p).write_bytes(b'x')),
                         self.item, str(self.root))
        self.assertEqual(list(self.root.iterdir()), [partial])
        self.assertEqual((partial / 'live.flac').read_bytes(), b'keep')

    def test_cutoff_undated_and_invalid_concurrency_rejected(self):
        from traxdb_sync.local_daemon import download_one
        client = self.client(lambda *a, **kw: self.fail('network'))
        for date in ['2026-08-12', '2026-01-01', '', '../../escape', '2026-99-99']:
            with self.assertRaises(ValueError):
                download_one(client, dict(self.item, inferred_date=date), str(self.root))
        for workers in [0, -1, 17, '4']:
            with self.assertRaises(ValueError):
                download_one(client, self.item, str(self.root), max_workers=workers)
        self.assertEqual(list(self.root.iterdir()), [])
        client.clone.assert_not_called()

    def test_pixeldrain_clone_has_independent_authenticated_session(self):
        from traxdb_sync.pixeldrain import PixeldrainClient
        client = PixeldrainClient('test-key', timeout_s=12)
        clone = client.clone()
        self.addCleanup(client.close)
        self.addCleanup(clone.close)
        self.assertIsNot(client._session, clone._session)
        self.assertEqual(clone._session.auth.password, 'test-key')
        self.assertEqual(clone._timeout, 12)


class PublishTests(unittest.TestCase):
    def test_atomic_publish_refuses_existing_empty_directory(self):
        from pathlib import Path
        from traxdb_sync.local_daemon import publish_folder
        with tempfile.TemporaryDirectory() as root:
            staging, dest = Path(root) / 'stage', Path(root) / 'dest'
            staging.mkdir()
            (staging / 'file').write_bytes(b'abc')
            dest.mkdir()
            inode = dest.stat().st_ino
            with self.assertRaises(FileExistsError):
                publish_folder(str(staging), str(dest))
            self.assertEqual(dest.stat().st_ino, inode)
            self.assertEqual(list(dest.iterdir()), [])
            self.assertEqual((staging / 'file').read_bytes(), b'abc')

    def test_pixeldrain_retries_are_bounded_and_verify_size(self):
        from pathlib import Path
        from unittest.mock import Mock
        from traxdb_sync.pixeldrain import PixeldrainClient, PixeldrainError
        client = PixeldrainClient()
        self.addCleanup(client.close)
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b'x']
        with tempfile.TemporaryDirectory() as root, patch.object(client._session, 'get', return_value=response) as get, patch('traxdb_sync.pixeldrain.time.sleep'):
            with self.assertRaises(PixeldrainError):
                client.download_file('id', str(Path(root) / 'track'), expected_size=3, max_retries=3)
            self.assertEqual(get.call_count, 3)


if __name__ == '__main__':
    unittest.main()
