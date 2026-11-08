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


if __name__ == '__main__':
    unittest.main()
