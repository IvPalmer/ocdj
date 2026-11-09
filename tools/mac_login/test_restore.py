"""Temporary homes, synthetic credentials/maps and fake SSH only."""
import io
import json
import os
from pathlib import Path
import plistlib
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from tools.mac_login import install, restore
from tools.instagram import foreground as fg
from tools.mac_library import helper


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.source = Path(__file__).resolve().parents[2]
        self.token = self.home / 'drain.env'
        self.token.write_text('OTHER=$(must-never-execute)\nexport DRAIN_TOKEN="synthetic-only"\n')
        self.token.chmod(0o600)
        self.root = self.home / 'audio'; self.root.mkdir()
        self.local_map = self.home / 'map.json'
        self.data = dict(version=1, root=str(self.root), collection_id='12345678-1234-5678-9abc-123456789abc',
                         tracks={'a' * 64: 'synthetic.wav'})
        self.local_map.write_text(json.dumps(self.data)); self.local_map.chmod(0o600)
        self.config = dict(token_file=str(self.token), root=str(self.root), map=str(self.local_map),
                           ffmpeg=sys.executable)

    def stage(self):
        return install.install(self.home, self.source, Path(sys.executable), self.token,
                               self.root, self.local_map, Path(sys.executable), ('playback', 'instagram'))

    def test_stage_is_durable_private_no_start_or_credential_read(self):
        with patch.object(subprocess, 'Popen') as spawn, patch.object(restore, 'private_text') as read:
            base = self.stage()
        spawn.assert_not_called(); read.assert_not_called()
        for mode, label in install.LABELS.items():
            plist = self.home / 'Library/LaunchAgents' / (label + '.plist')
            data = plistlib.loads(plist.read_bytes())
            self.assertEqual(data['LimitLoadToSessionType'], 'Aqua')
            self.assertTrue(data['RunAtLoad'])
            for forbidden in ('KeepAlive', 'StartInterval', 'StartCalendarInterval', 'WatchPaths'):
                self.assertNotIn(forbidden, data)
            self.assertNotIn('DRAIN_TOKEN', str(data))
            self.assertNotIn(str(self.source), str(data))
            self.assertEqual(data['ProgramArguments'][0], sys.executable)
            self.assertEqual(plist.stat().st_mode & 0o777, 0o600)
            if sys.platform == 'darwin':
                subprocess.run(['/usr/bin/plutil', '-lint', '-s', str(plist)], check=True)
        self.assertEqual(base.stat().st_mode & 0o777, 0o700)
        self.assertEqual((base / 'config.json').stat().st_mode & 0o777, 0o600)
        self.assertNotIn('synthetic-only', (base / 'config.json').read_text())
        self.assertTrue((base / 'code/tools/instagram/connector.py').exists())
        result = subprocess.run([sys.executable, '-m', 'tools.mac_login.restore', '--help'],
                                cwd=base / 'code', capture_output=True, text=True,
                                env={k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'DRAIN_TOKEN')})
        self.assertEqual(result.returncode, 0, result.stderr)
        # The launcher imports lazily; exercise the staged child as well.
        child = subprocess.run([sys.executable, 'tools/instagram/connector.py', '--help'],
                               cwd=base / 'code', capture_output=True, text=True,
                               env={k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'DRAIN_TOKEN')})
        self.assertEqual(child.returncode, 0, child.stderr)
        with self.assertRaises(ValueError):
            self.stage()
        self.assertTrue((base / 'code/tools/mac_login/restore.py').exists())

    def test_failed_install_removes_only_its_own_files(self):
        marker = self.home / 'Library/LaunchAgents/unrelated.plist'
        marker.parent.mkdir(parents=True); marker.write_text('leave alone')
        with patch.object(install.plistlib, 'dump', side_effect=OSError), self.assertRaises(OSError):
            self.stage()
        self.assertEqual(marker.read_text(), 'leave alone')
        self.assertEqual(list(marker.parent.iterdir()), [marker])
        self.assertFalse((self.home / 'Library/Application Support/OCDJ/login-connectors').exists())

    def test_token_is_parsed_without_shell_and_private_gate(self):
        self.assertEqual(restore.token_from_file(self.token), 'synthetic-only')
        self.token.chmod(0o644)
        with self.assertRaises(helper.Refused): restore.token_from_file(self.token)
        self.token.chmod(0o600)
        link = self.home / 'link'; link.symlink_to(self.token)
        with self.assertRaises(OSError): restore.token_from_file(link)
        self.token.write_text('DRAIN_TOKEN=a\nDRAIN_TOKEN=b\n')
        with self.assertRaises(helper.Refused): restore.token_from_file(self.token)

    def test_instagram_login_never_prompts_or_retries(self):
        config = self.home / 'config.json'; config.write_text(json.dumps(self.config)); config.chmod(0o600)
        with patch('sys.argv', ['restore', 'instagram', '--config', str(config)]), \
             patch.object(restore.signal, 'signal'), patch.dict(os.environ, {}, clear=True), \
             patch.object(fg, 'run', side_effect=RuntimeError('synthetic-only secret detail')) as run, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(restore.main(), 1)
        run.assert_called_once_with(600, resident=True, allow_keychain_prompt=False)
        self.assertNotIn('synthetic-only', output.getvalue())
        self.assertIn('no-retry', output.getvalue())

    def test_login_worker_survives_terminal_hangup(self):
        log = self.home / 'connector.log'
        marker = self.home / 'terminal-closed'
        worker = """import os, sys, time
from pathlib import Path
assert not os.isatty(0)
print(os.getpid(), flush=True)
while not Path(sys.argv[1]).exists():
    time.sleep(0.05)
for _ in range(600):
    print('poll', flush=True)
    time.sleep(0.05)
"""
        launcher = (
            "from pathlib import Path; import sys, time; from tools.mac_login.restore import detach; "
            "detach([sys.executable, '-u', '-c', sys.argv[2], sys.argv[3]], Path(sys.argv[1])); "
            "print('ready', flush=True); time.sleep(30)"
        )
        pid, terminal = pty.fork()
        if pid == 0:
            os.execv(sys.executable, [sys.executable, '-c', launcher, str(log), worker, str(marker)])
        worker_pid = None
        try:
            self.assertTrue(select.select([terminal], [], [], 5)[0])
            self.assertEqual(os.read(terminal, 4096).strip(), b'ready')
            os.close(terminal)
            terminal = None
            marker.touch()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                lines = log.read_text().splitlines()
                if lines:
                    worker_pid = int(lines[0])
                if lines.count('poll') >= 2:
                    break
                time.sleep(0.05)
            self.assertGreaterEqual(lines.count('poll'), 2)
            self.assertEqual(os.getsid(worker_pid), worker_pid)
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        finally:
            if terminal is not None:
                os.close(terminal)
            for process in (pid, worker_pid):
                if process is not None:
                    try:
                        os.kill(process, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            os.waitpid(pid, 0)

    def test_detach_refuses_nonprivate_log(self):
        log = self.home / 'connector.log'
        log.touch(mode=0o644)
        with patch.object(subprocess, 'Popen') as spawn, self.assertRaises(helper.Refused):
            restore.detach(['unused'], log)
        spawn.assert_not_called()

    def test_playback_holds_real_singleton_and_cleans_tunnel_after_ambiguity(self):
        processes = []
        real_popen = subprocess.Popen
        def spawn(command, **kwargs):
            self.assertIn('127.0.0.1:', ' '.join(command))
            self.assertNotIn('DRAIN_TOKEN', kwargs['env'])
            process = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            processes.append(process)
            return process
        lock = self.home / 'playback.lock'
        original_singleton = helper.singleton
        def claim(api, data, **kwargs):
            with self.assertRaises(BlockingIOError), original_singleton(lock): pass
            self.assertEqual(data, self.data)
            raise TimeoutError('ambiguous result')
        with patch.object(helper, 'singleton', side_effect=lambda: original_singleton(lock)), \
             patch.object(helper, 'serve_one', side_effect=claim) as serve, \
             patch.object(fg, 'backend_address', return_value='172.22.0.5'), \
             patch.object(fg, 'wait_tunnel'), patch.object(subprocess, 'Popen', side_effect=spawn), \
             patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic-only'}):
            with self.assertRaises(TimeoutError): restore.playback(self.config)
        serve.assert_called_once()
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        with original_singleton(lock): pass

    def test_existing_audio_worker_blocks_before_ssh(self):
        original_singleton = helper.singleton
        lock = self.home / 'playback.lock'
        with original_singleton(lock), \
             patch.object(helper, 'singleton', side_effect=lambda: original_singleton(lock)), \
             patch.object(fg, 'backend_address') as discover:
            with self.assertRaises(BlockingIOError): restore.playback(self.config)
        discover.assert_not_called()

    def test_instagram_lock_crosses_tmp_context_and_honors_legacy(self):
        stable = self.home / 'stable.lock'
        old = self.home / 'old'; old.mkdir()
        new = self.home / 'new'; new.mkdir()
        with patch.object(fg, 'LOGIN_LOCK', stable):
            with patch.object(fg.tempfile, 'gettempdir', return_value=str(old)), fg.singleton():
                with patch.object(fg.tempfile, 'gettempdir', return_value=str(new)):
                    with self.assertRaisesRegex(fg.RunError, 'another-foreground'):
                        with fg.singleton(): pass
            legacy = old / f'ocdj-instagram-foreground-{os.getuid()}.lock'
            with helper.singleton(legacy), patch.object(fg.tempfile, 'gettempdir', return_value=str(old)):
                with self.assertRaisesRegex(fg.RunError, 'another-foreground'):
                    with fg.singleton(): pass
            with patch.object(fg.tempfile, 'gettempdir', return_value=str(new)), fg.singleton(): pass


if __name__ == '__main__':
    unittest.main()
