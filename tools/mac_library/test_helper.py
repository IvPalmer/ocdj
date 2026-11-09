"""Synthetic private maps/audio only; no real library, tokens, API or SSH."""
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import signal
import unittest
from unittest.mock import patch
import wave

from tools.mac_library import helper


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'library'; self.root.mkdir()
        self.collection = '12345678-1234-5678-9abc-123456789abc'
        self.opaque = 'a' * 64
        self.audio = self.root / 'fixture.wav'
        with wave.open(str(self.audio), 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
            wav.writeframes(b'\0\0' * 8000)
        self.data = dict(version=1, collection_id=self.collection,
                         root=str(self.root), tracks={self.opaque: 'fixture.wav'})
        self.map = self.base / 'map.json'
        self.map.write_text(json.dumps(self.data)); self.map.chmod(0o600)

    def test_exact_private_map_and_seekable_descriptor(self):
        data = helper.load_map(self.map, str(self.root))
        with helper.source(data, self.collection, self.opaque) as (fd, demuxer):
            self.assertEqual(os.read(fd, 4), b'RIFF')
            self.assertEqual(demuxer, 'wav')

    def test_wrong_scope_and_map_permissions(self):
        with self.assertRaises(helper.Refused):
            helper.load_map(self.map, str(self.base))
        self.map.chmod(0o644)
        with self.assertRaises(helper.Refused):
            helper.load_map(self.map, str(self.root))
        self.map.chmod(0o600)
        link = self.base / 'link'; link.symlink_to(self.map)
        with self.assertRaises(OSError):
            helper.load_map(link, str(self.root))

    def test_exact_lookup_no_traversal_symlinks_or_special_files(self):
        for collection, ident in [('other', self.opaque), (self.collection, 'b' * 64),
                                   (self.collection, '../fixture.wav'), (self.collection, self.opaque + '\n')]:
            with self.assertRaises(helper.Refused), helper.source(self.data, collection, ident):
                pass
        (self.root / 'link.wav').symlink_to(self.audio)
        (self.root / 'nested').symlink_to(self.root, target_is_directory=True)
        os.mkfifo(self.root / 'fifo.wav')
        for relative in ['../fixture.wav', '/fixture.wav', 'ID3/fixture.wav', 'TraxDB/fixture.wav',
                         'link.wav', 'nested/fixture.wav', 'fifo.wav', 'missing.wav', 'fixture.txt', './fixture.wav']:
            self.data['tracks'][self.opaque] = relative
            with self.subTest(relative=relative), self.assertRaises((helper.Refused, OSError)), helper.source(self.data, self.collection, self.opaque):
                pass

    def test_changed_and_oversized_source_refused(self):
        with patch.object(helper, 'MAX_SOURCE', 1), self.assertRaises(helper.Refused), helper.source(self.data, self.collection, self.opaque):
            pass
        with self.assertRaises(helper.Refused):
            with helper.source(self.data, self.collection, self.opaque):
                self.audio.write_bytes(b'changed')

    def test_actual_transcode_uses_synthetic_audio(self):
        from mutagen.mp3 import MP3
        output = self.base / 'audio.mp3'
        with helper.source(self.data, self.collection, self.opaque) as (fd, demuxer):
            helper.transcode(fd, demuxer, output, os.environ.get('TEST_FFMPEG', 'ffmpeg'))
        mp3 = MP3(output)
        self.assertGreater(mp3.info.length, .9)
        self.assertEqual(mp3.info.sample_rate, 48000)
        self.assertFalse(mp3.tags and mp3.tags.get('TIT2'))
        with helper.source(self.data, self.collection, self.opaque) as (fd, demuxer):
            with patch.object(helper, 'MAX_OUTPUT', 100), self.assertRaises(helper.Refused):
                helper.transcode(fd, demuxer, output, os.environ.get('TEST_FFMPEG', 'ffmpeg'))

    def test_api_is_private_no_proxy_no_redirect_no_retry(self):
        for url in ['https://example.invalid', 'http://localhost:1', 'http://127.0.0.1:1/?x=1',
                    'http://user@127.0.0.1:1', 'http://127.0.0.1:1/path']:
            with self.assertRaises(helper.Refused):
                helper.API(url, 'fixture')
        api = helper.API('http://127.0.0.1:1', 'fixture')
        self.assertFalse(api.session.trust_env)
        with patch.object(api.session, 'post') as post:
            post.return_value.status_code = 302
            with self.assertRaises(helper.Refused):
                api.post('claim/', data={})
            self.assertEqual(post.call_count, 1)
            self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_normal_claim_upload_and_ambiguous_result_never_resent(self):
        from unittest.mock import Mock
        job = dict(track_id=1, collection_id=self.collection, opaque_id=self.opaque, token='b' * 48)
        api = Mock()
        uploaded = []
        def post(endpoint, **kwargs):
            if endpoint == 'claim/':
                return job
            uploaded.append(kwargs['audio'].read())
            raise helper.requests.Timeout()
        api.post.side_effect = post
        with self.assertRaises(helper.requests.Timeout):
            helper.serve_one(api, self.data, ffmpeg=os.environ.get('TEST_FFMPEG', 'ffmpeg'))
        self.assertEqual(api.post.call_count, 2)  # claim, one upload; no failure/resend
        self.assertGreater(len(uploaded[0]), 12)
        api.reset_mock()
        api.post.side_effect = [job, {}]
        self.data['tracks'][self.opaque] = 'missing.wav'
        self.assertTrue(helper.serve_one(api, self.data))
        self.assertEqual(api.post.call_args.kwargs['data'], {})

    def test_singleton_rejects_duplicate_then_releases_without_unlink(self):
        lock = self.base / 'lock'
        with helper.singleton(lock):
            with self.assertRaises(BlockingIOError), helper.singleton(lock):
                pass
        with helper.singleton(lock):
            self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        lock.chmod(0o644)
        with self.assertRaises(helper.Refused), helper.singleton(lock):
            pass
        lock.unlink()
        lock.symlink_to(self.map)
        with self.assertRaises(OSError), helper.singleton(lock):
            pass

    def test_bounded_default_and_login_session_beyond_fifteen_minutes(self):
        from unittest.mock import Mock
        with patch.object(helper.time, 'monotonic', side_effect=[0, 0, 900]), \
                patch.object(helper.time, 'sleep'), patch.object(helper, 'serve_one', return_value=True) as serve:
            helper.listen(Mock(), self.data)
            self.assertEqual(serve.call_count, 1)
        with patch.object(helper.time, 'monotonic', return_value=99999), \
                patch.object(helper.time, 'sleep'), \
                patch.object(helper, 'serve_one', side_effect=[False, True, helper.requests.Timeout()]) as serve:
            with self.assertRaises(helper.requests.Timeout):
                helper.listen(Mock(), self.data, seconds=None)
            self.assertEqual(serve.call_count, 3)  # No retry of the ambiguous claim.

    def test_login_session_signals_stop_and_release_lock(self):
        # A real foreground subprocess, but fake claims and no API/SSH/token.
        code = """
import signal, sys
from tools.mac_library import helper
for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
    signal.signal(sig, helper.interrupted)
helper.serve_one = lambda *a, **k: False
try:
    with helper.singleton(sys.argv[1]):
        print('ready', flush=True)
        helper.listen(None, {}, seconds=None)
except KeyboardInterrupt:
    raise SystemExit(130)
"""
        lock = self.base / 'signal-lock'
        for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            child = subprocess.Popen([sys.executable, '-c', code, str(lock)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                child.send_signal(sig)
                self.assertEqual(child.wait(timeout=5), 130)
                with helper.singleton(lock):
                    pass
            finally:
                if child.poll() is None:
                    child.kill(); child.wait()
                child.stdout.close()

    def test_login_mode_keeps_exact_map_and_bounded_mode_validation(self):
        args = ['helper', '--api', 'http://127.0.0.1:1', '--root', str(self.root),
                '--map', str(self.map)]
        with patch.object(sys, 'argv', args + ['--login-session']), \
                patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic-fixture'}, clear=True), \
                patch.object(helper, 'singleton') as lock, patch.object(helper, 'API'), \
                patch.object(helper, 'listen') as listen:
            self.assertEqual(helper.main(), 0)
            self.assertIsNone(listen.call_args.kwargs['seconds'])
            self.assertEqual(listen.call_args.args[1], self.data)
        with patch.object(sys, 'argv', args + ['--seconds', '1801']), \
                patch.object(helper, 'API') as api:
            self.assertEqual(helper.main(), 1)
            api.assert_not_called()
        with patch.object(sys, 'argv', args + ['--login-session', '--seconds', '30']):
            with self.assertRaises(SystemExit):
                helper.main()


if __name__ == '__main__':
    unittest.main()
