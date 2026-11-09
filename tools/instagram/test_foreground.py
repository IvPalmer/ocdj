"""DB-free lifecycle tests; no real SSH, Instagram, cookies or API server."""
import os
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.instagram import foreground as fg


class ForegroundTests(unittest.TestCase):
    def test_bounds(self):
        for value in ('0', '-1', '3601'):
            with self.assertRaises(ValueError):
                fg.seconds(value)
        self.assertEqual(fg.seconds('600'), 600)

    def test_recovery_only_repeats_the_auth_probe(self):
        with patch.object(fg, 'probe', side_effect=[ConnectionResetError(), None]) as probe, \
             patch.object(fg.time, 'sleep') as sleep:
            fg.wait_backend(12345, 'fixture-secret')
        self.assertEqual(probe.call_count, 2)
        sleep.assert_called_once()
        with patch.object(fg, 'probe', side_effect=fg.RunError('private-auth-validation-failed')) as probe, \
             patch.object(fg.time, 'sleep') as sleep, self.assertRaises(fg.RunError):
            fg.wait_backend(12345, 'fixture-secret')
        probe.assert_called_once()
        sleep.assert_not_called()

    def test_tunnel_target_stays_on_vps_loopback(self):
        with patch.dict(os.environ, {'DRAIN_TOKEN': 'fixture-secret'}), \
             patch.object(fg.subprocess, 'Popen') as spawn:
            fg.start_tunnel(12345)
        command = spawn.call_args.args[0]
        self.assertEqual(command[command.index('-L') + 1], '127.0.0.1:12345:127.0.0.1:18002')
        self.assertNotIn('DRAIN_TOKEN', spawn.call_args.kwargs['env'])

    def test_probe_rejects_redirects_and_unexpected_responses(self):
        import urllib.error
        for code in (200, 302, 401, 404, 503):
            opener = unittest.mock.Mock()
            opener.open.side_effect = urllib.error.HTTPError('http://localhost', code, '', {}, None)
            with patch.object(fg.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(fg.RunError):
                    fg.probe(12345, 'fixture-secret')
        opener.open.side_effect = urllib.error.HTTPError('http://localhost', 400, '', {}, io.BytesIO(b'{"collection_id": ["required"], "posts": ["required"]}'))
        with patch.object(fg.urllib.request, 'build_opener', return_value=opener):
            fg.probe(12345, 'fixture-secret')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.data, b'{}')
        self.assertTrue(request.full_url.endswith('/connector/sync/'))
        opener.open.side_effect = urllib.error.HTTPError('http://localhost', 400, '', {}, io.BytesIO(b'<html>Bad Request</html>'))
        with patch.object(fg.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(fg.RunError):
                fg.probe(12345, 'fixture-secret')
        opener.open.side_effect = None
        opener.open.return_value = unittest.mock.MagicMock()
        with patch.object(fg.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(fg.RunError):
                fg.probe(12345, 'fixture-secret')

    def test_missing_token_stops_before_ssh(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(fg, 'start_tunnel') as discover:
            with self.assertRaises(fg.RunError):
                fg.run(1)
            discover.assert_not_called()

    def test_denied_cookie_access_stops_before_ssh(self):
        with patch.dict(os.environ, {'DRAIN_TOKEN': 'fixture-secret'}), \
             patch.object(Path, 'open', side_effect=PermissionError), \
             patch.object(fg, 'start_tunnel') as discover:
            with self.assertRaises(fg.RunError):
                fg.run(1)
            discover.assert_not_called()

    def test_real_process_deadline_tunnel_loss_and_normal_exit(self):
        for tunnel_exit, child_exit, expected in ((False, None, 124), (True, None, 1), (False, 0, 0), (False, 1, 1)):
            with self.subTest(tunnel_exit=tunnel_exit, child_exit=child_exit):
                tunnel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
                child = subprocess.Popen([sys.executable, '-c', f'raise SystemExit({child_exit})' if child_exit is not None else 'import time; time.sleep(60)'], start_new_session=True)
                try:
                    if tunnel_exit:
                        tunnel.terminate(); tunnel.wait()
                    result = fg.supervise(tunnel, child, time.monotonic() + .15)
                    self.assertEqual(result, expected)
                finally:
                    fg.stop(child); fg.stop(tunnel)
                self.assertIsNotNone(child.poll())
                self.assertIsNotNone(tunnel.poll())

    def test_run_cleans_temp_directory_and_children_on_interrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_cookie = Path(directory) / 'Cookies'
            fake_cookie.write_bytes(b'SQLite format 3\x00')
            processes, child_envs = [], []
            real_popen = subprocess.Popen
            def spawn(command, **kwargs):
                if '--work-seconds' in command:
                    self.assertNotIn('fixture-secret', ' '.join(command))
                    child_envs.append(kwargs['env'])
                    (Path(kwargs['env']['TMPDIR']) / 'owned-media').write_bytes(b'fixture')
                else:
                    self.assertNotIn('DRAIN_TOKEN', kwargs['env'])
                    self.assertIn('127.0.0.1:', ' '.join(command))
                process = real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
                processes.append(process)
                return process
            with patch.dict(os.environ, {'DRAIN_TOKEN': 'fixture-secret', 'HTTP_PROXY': 'http://invalid'}), \
                 patch.object(fg, 'COOKIE_DB', fake_cookie), \
                 patch.object(fg.tempfile, 'gettempdir', return_value=directory), \
                 patch.object(fg, 'LOGIN_LOCK', Path(directory) / 'stable.lock'), \
                 patch.object(fg, 'wait_tunnel'), patch.object(fg, 'probe'), \
                 patch.object(fg.subprocess, 'Popen', side_effect=spawn), \
                 patch.object(fg, 'supervise', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    fg.run(1)
            self.assertTrue(processes)
            self.assertTrue(all(p.poll() is not None for p in processes))
            self.assertNotIn('HTTP_PROXY', child_envs[0])
            self.assertEqual(child_envs[0]['DRAIN_TOKEN'], 'fixture-secret')
            self.assertFalse(Path(child_envs[0]['TMPDIR']).exists())

    def test_stop_kills_descendant_even_when_it_ignores_term(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'pid'
            descendant = ('import os,signal,time; from pathlib import Path; '
                          'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                          f'Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)')
            parent_code = ('import subprocess,sys,time; '
                           f'subprocess.Popen([sys.executable, "-c", {descendant!r}]); time.sleep(60)')
            parent = subprocess.Popen([sys.executable, '-c', parent_code], start_new_session=True)
            try:
                end = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < end:
                    time.sleep(.02)
                self.assertTrue(marker.exists())
                pid = int(marker.read_text())
                fg.stop(parent)
                # A killed orphan can briefly remain as a zombie until reaped.
                end = time.monotonic() + 2
                while time.monotonic() < end:
                    state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                                           capture_output=True, text=True).stdout.strip()
                    if not state or state.startswith('Z'):
                        break
                    time.sleep(.02)
                self.assertTrue(not state or state.startswith('Z'), state)
            finally:
                fg.stop(parent)


if __name__ == '__main__':
    unittest.main()
