"""Synthetic resident lifecycle fixtures. No OS secret, SSH, or live API calls."""
import ctypes
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch, call

import requests
from tools.instagram import connector as c, foreground as fg, mac_access


class RetryTests(unittest.TestCase):
    def test_safe_reads_recover_and_close_transient_responses(self):
        unavailable = Mock(status_code=503)
        ok = Mock(status_code=200)
        request = Mock(side_effect=[requests.Timeout('private'), unavailable, ok])
        with patch.object(c.time, 'sleep') as sleep:
            self.assertIs(c.safe_request(request), ok)
        self.assertEqual(sleep.call_args_list, [call(5), call(15)])
        unavailable.close.assert_called_once()

    def test_network_budget_exhausts_and_restrictions_never_retry(self):
        with patch.object(c.time, 'sleep') as sleep:
            request = Mock(side_effect=requests.ConnectionError('private'))
            with self.assertRaisesRegex(c.ConnectorError, '^network-unavailable$'):
                c.safe_request(request)
            self.assertEqual(request.call_count, 4)
            self.assertEqual(sleep.call_args_list, [call(5), call(15), call(30)])
        for code in [301, 302, 303, 307, 308, 401, 403, 429, 500]:
            session = Mock()
            session.get.return_value.status_code = code
            with patch.object(c.time, 'sleep') as sleep, self.assertRaises(c.ConnectorError):
                c.instagram_json(session, '/synthetic/')
            session.get.assert_called_once()
            sleep.assert_not_called()

    def test_mutations_never_replay_ambiguous_network_or_5xx(self):
        for endpoint in ['claim/', 'sync/', 'thumbnails/', 'media/1/upload/', 'media/1/complete/']:
            for outcome in [requests.Timeout('private'), Mock(status_code=503)]:
                api = c.API('https://fixture.invalid', 'synthetic')
                api.session.post = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
                with patch.object(c.time, 'sleep') as sleep, self.assertRaises(c.ConnectorError):
                    api.post(endpoint, {})
                api.session.post.assert_called_once()
                sleep.assert_not_called()

    def test_resident_recovers_transport_without_repeating_mutation(self):
        for endpoint in ['claim/', 'sync/', 'thumbnails/', 'media/1/upload/', 'media/1/complete/']:
            api = c.API('http://127.0.0.1:12345', 'synthetic', resident=True)
            api.session.post = Mock(side_effect=requests.ConnectionError())
            with patch.object(c, 'wait_backend') as recover:
                self.assertIsNone(api.post(endpoint, {}))
            api.session.post.assert_called_once()
            recover.assert_called_once_with(12345, 'synthetic')
        api.session.post = Mock(return_value=Mock(status_code=401))
        with patch.object(c, 'wait_backend') as recover, self.assertRaises(c.ConnectorError):
            api.post('claim/')
        recover.assert_not_called()

    def test_video_cdn_restrictions_also_fail_closed(self):
        from unittest.mock import MagicMock
        for code, status in [(302, 'restricted'), (401, 'session-expired'), (403, 'restricted'), (429, 'rate-limited')]:
            response = MagicMock(status_code=code)
            response.__enter__.return_value = response
            with patch.object(c, 'cdn_url', return_value='https://synthetic.invalid/'), \
                 patch.object(c.requests, 'get', return_value=response) as request, \
                 self.assertRaisesRegex(c.ConnectorError, '^' + status + '$'):
                c.download('https://synthetic.invalid/', Path('/unused-synthetic'))
            request.assert_called_once()

    def test_heartbeat_recovers_but_auth_rejection_stops(self):
        api = c.API('https://fixture.invalid', 'synthetic')
        ok = Mock(status_code=200); ok.json.return_value = {}
        api.session.post = Mock(side_effect=[requests.ConnectionError(), ok])
        with patch.object(c.time, 'sleep'):
            self.assertEqual(api.post('heartbeat/', {'status': 'ready'}), {})
        self.assertEqual(api.session.post.call_count, 2)
        api.session.post = Mock(return_value=Mock(status_code=401))
        with patch.object(c.time, 'sleep') as sleep, self.assertRaises(c.ConnectorError):
            api.post('heartbeat/', {'status': 'ready'})
        sleep.assert_not_called()


class ResidentTests(unittest.TestCase):
    def drive(self, claims=12, gap=15, restriction=False, allow_prompt=False):
        self.claims, self.syncs, self.reads = 0, 0, 0
        self.clock = 0
        api, session = Mock(), Mock()
        args = SimpleNamespace(api='https://fixture.invalid', profile='Profile 1',
                               fixture=None, work_seconds=0, resident=True,
                               allow_keychain_prompt=allow_prompt)
        def read(*args, **kwargs):
            self.reads += 1
            if restriction and self.reads == 2: raise c.ConnectorError('restricted')
            return {'posts': []}, {}
        def post(endpoint, *args, **kwargs):
            if endpoint == 'sync/':
                self.syncs += 1
                return {'media': []}
            if endpoint == 'claim/':
                self.claims += 1
                if self.claims > claims: raise KeyboardInterrupt
                return {'id': self.claims, 'instagram_id': '1', 'post_instagram_id': '1', 'position': 0, 'token': 'synthetic'}
        api.post.side_effect = post
        def sleep(seconds): self.clock += gap
        with patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic'}), \
             patch.object(c, 'API', return_value=api), \
             patch.object(c, 'instagram_session', return_value=session) as extract, \
             patch.object(c, 'read_collection', side_effect=read), \
             patch.object(c, 'read_comments', return_value=[{'id': '1', 'text': 'ID: Artist - Song'}]), \
             patch.object(c.time, 'sleep', side_effect=sleep), \
             patch.object(c.time, 'monotonic', side_effect=lambda: self.clock), \
             patch.object(c.time, 'time', side_effect=lambda: self.clock):
            try:
                c.run(args)
            finally:
                extract.assert_called_once_with('Profile 1', allow_keychain_prompt=allow_prompt)
                session.close.assert_called_once()

    def test_prompt_is_startup_only_across_hourly_and_wake_refresh(self):
        with self.assertRaises(KeyboardInterrupt): self.drive(claims=242, allow_prompt=True)
        self.assertEqual(self.syncs, 2)
        with self.assertRaises(KeyboardInterrupt): self.drive(claims=1, gap=7200, allow_prompt=True)
        self.assertEqual(self.syncs, 2)

    def test_prompt_flag_rejected_before_access_without_live_resident(self):
        for resident, fixture in [(False, None), (False, 'fixture.json'), (True, 'fixture.json')]:
            with patch.object(c, 'instagram_session') as extract, patch.object(c, 'API') as api, \
                 self.assertRaisesRegex(c.ConnectorError, 'keychain-prompt-requires-live-resident'):
                c.run(SimpleNamespace(resident=resident, fixture=fixture, allow_keychain_prompt=True))
            extract.assert_not_called()
            api.assert_not_called()

    def test_bounded_mode_keeps_existing_helper_and_closes_session(self):
        args = SimpleNamespace(api='https://fixture.invalid', profile='Profile 1', fixture=None,
                               work_seconds=0, resident=False)
        session, api = Mock(), Mock()
        api.post.side_effect = lambda endpoint, *a, **k: {'media': []} if endpoint == 'sync/' else None
        with patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic'}), patch.object(c, 'API', return_value=api), \
             patch.object(c, 'instagram_session', return_value=session) as extract, \
             patch.object(c, 'read_collection', return_value=({'posts': []}, {})):
            c.run(args)
        extract.assert_called_once_with('Profile 1', allow_keychain_prompt=True)
        session.close.assert_called_once()

    def test_keychain_denial_is_sanitized_and_stops_before_sync_without_retry(self):
        args = SimpleNamespace(api='https://fixture.invalid', profile='Profile 1', fixture=None,
                               work_seconds=0, resident=True, allow_keychain_prompt=True)
        api = Mock()
        with patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic'}), patch.object(c, 'API', return_value=api), \
             patch.object(c, 'instagram_session', side_effect=c.ConnectorError('keychain-access-required')) as extract, \
             patch.object(c.time, 'sleep') as sleep, \
             self.assertRaisesRegex(c.ConnectorError, '^keychain-access-required$'):
            c.run(args)
        extract.assert_called_once()
        sleep.assert_not_called()
        api.post.assert_called_once_with('heartbeat/', {'status': 'error'})

    def test_more_than_ten_jobs_without_resync_or_restart(self):
        with self.assertRaises(KeyboardInterrupt): self.drive()
        self.assertEqual(self.claims, 13)
        self.assertEqual(self.syncs, 1)
        self.assertEqual(self.reads, 1)

    def test_hourly_refresh_is_not_per_job(self):
        with self.assertRaises(KeyboardInterrupt): self.drive(claims=242)
        self.assertEqual(self.syncs, 2)
        self.assertEqual(self.reads, 2)

    def test_wake_refreshes_once_and_does_not_catch_up_missed_hours(self):
        with self.assertRaises(KeyboardInterrupt): self.drive(claims=1, gap=7200)
        self.assertEqual(self.syncs, 2)
        self.assertEqual(self.claims, 2)

    def test_refresh_restriction_stops_before_next_claim_and_sync(self):
        with self.assertRaisesRegex(c.ConnectorError, '^restricted$'):
            self.drive(claims=2, gap=7200, restriction=True)
        self.assertEqual(self.syncs, 1)
        self.assertEqual(self.claims, 1)

    def test_resident_idle_does_not_expire_work_seconds(self):
        api = Mock()
        polls = []
        def post(endpoint, *args, **kwargs):
            if endpoint == 'sync/': return {'media': []}
            if endpoint == 'claim/':
                polls.append(endpoint)
                if len(polls) == 5: raise KeyboardInterrupt
        api.post.side_effect = post
        args = SimpleNamespace(api='https://fixture.invalid', profile='Profile 1', fixture=None, work_seconds=0, resident=True)
        with patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic'}), \
             patch.object(c, 'API', return_value=api), patch.object(c, 'instagram_session'), \
             patch.object(c, 'read_collection', return_value=({'posts': []}, {})), \
             patch.object(c.time, 'sleep') as sleep, self.assertRaises(KeyboardInterrupt):
            c.run(args)
        self.assertEqual(len(polls), 5)
        self.assertEqual(sleep.call_args_list, [call(15)] * 4)

    def test_fixture_cannot_become_infinite_worker(self):
        with patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic'}), self.assertRaisesRegex(c.ConnectorError, 'resident-fixture'):
            c.run(SimpleNamespace(api='https://fixture.invalid', fixture='fixture.json', resident=True))


class SupervisorTests(unittest.TestCase):
    def test_tunnel_start_auth_failure_is_not_network_retry(self):
        for detail, expected in [(b'Permission denied (publickey)', fg.RunError),
                                 (b'Connection refused', ConnectionError)]:
            tunnel = Mock(stderr=io.BytesIO(detail))
            tunnel.poll.return_value = 255
            with self.assertRaises(expected): fg.wait_tunnel(tunnel, 12345, float('inf'))

    def test_reconnect_same_worker_and_stop_on_worker_failure(self):
        tunnel, child, reconnect = Mock(), Mock(), Mock()
        tunnel.poll.side_effect = [1, None]
        child.poll.side_effect = [None, None, 1]
        with patch.object(fg.time, 'sleep'):
            self.assertEqual(fg.supervise(tunnel, child, float('inf'), reconnect), 1)
        reconnect.assert_called_once_with()

    def test_dead_worker_does_not_trigger_transport_reconnect(self):
        child, tunnel, reconnect = Mock(), Mock(), Mock()
        child.poll.return_value = 1
        self.assertEqual(fg.supervise(tunnel, child, float('inf'), reconnect), 1)
        reconnect.assert_not_called()

    def test_flapping_transport_exhausts_budget(self):
        child, tunnel, reconnect = Mock(), Mock(), Mock()
        child.poll.return_value = None
        tunnel.poll.return_value = 1
        with patch.object(fg.time, 'sleep'), self.assertRaisesRegex(fg.RunError, 'tunnel-unstable'):
            fg.supervise(tunnel, child, float('inf'), reconnect)
        self.assertEqual(reconnect.call_count, 3)

    def test_preflight_only_reads_header_no_credentials_or_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / 'synthetic'
            cookie.write_bytes(b'SQLite format 3\x00' + b'not read or printed')
            with patch.object(fg, 'COOKIE_DB', cookie), patch.object(fg, 'start_tunnel') as ssh, \
                 patch.dict(os.environ, {}, clear=True), patch('sys.stdout', new_callable=io.StringIO) as output:
                fg.preflight()
            ssh.assert_not_called()
            self.assertNotIn('not read or printed', output.getvalue())
            cookie.write_bytes(b'wrong header')
            with patch.object(fg, 'COOKIE_DB', cookie), self.assertRaisesRegex(fg.RunError, 'unexpected-header'):
                fg.preflight()

    def test_resident_wrapper_uses_same_cleanup_and_single_child(self):
        for allow_prompt in (False, True):
            for interrupted in (False, True):
                with self.subTest(allow_prompt=allow_prompt, interrupted=interrupted):
                    self.check_resident_wrapper(allow_prompt, interrupted)

    def check_resident_wrapper(self, allow_prompt, interrupted):
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / 'Cookies'; cookie.write_bytes(b'SQLite format 3\x00')
            processes = []
            def spawn(command, **kwargs):
                process = Mock(pid=123)
                processes.append((command, process, kwargs))
                return process
            with patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic'}), \
                 patch.object(fg, 'COOKIE_DB', cookie), \
                 patch.object(fg.tempfile, 'gettempdir', return_value=directory), \
                 patch.object(fg, 'LOGIN_LOCK', Path(directory) / 'stable.lock'), \
                 patch.object(fg, 'wait_tunnel'), patch.object(fg, 'probe'), \
                 patch.object(fg.subprocess, 'Popen', side_effect=spawn), \
                 patch.object(fg, 'stop') as stop, \
                 patch.object(fg, 'supervise', side_effect=KeyboardInterrupt if interrupted else None,
                              return_value=1) as supervisor:
                if interrupted:
                    with self.assertRaises(KeyboardInterrupt):
                        fg.run(600, resident=True, allow_keychain_prompt=allow_prompt)
                else:
                    self.assertEqual(fg.run(600, resident=True, allow_keychain_prompt=allow_prompt), 1)
            self.assertEqual(len(processes), 2)
            self.assertIn('--resident', processes[1][0])
            self.assertEqual('--allow-keychain-prompt' in processes[1][0], allow_prompt)
            self.assertEqual(supervisor.call_args.args[2], float('inf'))
            self.assertEqual(stop.call_args_list[-2:], [call(processes[1][1]), call(processes[0][1])])
            self.assertFalse(Path(processes[1][2]['env']['TMPDIR']).exists())

    def test_prompt_flag_requires_resident_before_any_access(self):
        with patch.object(fg, 'preflight') as preflight, \
             self.assertRaisesRegex(fg.RunError, '^keychain-prompt-requires-resident$'):
            fg.run(600, allow_keychain_prompt=True)
        preflight.assert_not_called()
        for flags in [['--allow-keychain-prompt'], ['--preflight', '--allow-keychain-prompt']]:
            with patch('sys.argv', ['foreground.py'] + flags), patch('sys.stderr', new_callable=io.StringIO), \
                 patch.object(fg, 'preflight') as preflight, self.assertRaises(SystemExit) as error:
                fg.main()
            self.assertEqual(error.exception.code, 2)
            preflight.assert_not_called()


    def test_transport_retry_reuses_port_without_worker_relaunch(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / 'Cookies'; cookie.write_bytes(b'SQLite format 3\x00')
            commands = []
            def spawn(command, **kwargs):
                commands.append(command)
                return Mock(pid=123, poll=Mock(return_value=None))
            def supervise(tunnel, child, deadline, reconnect):
                reconnect()
                raise KeyboardInterrupt
            with patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic'}), \
                 patch.object(fg, 'COOKIE_DB', cookie), \
                 patch.object(fg.tempfile, 'gettempdir', return_value=directory), \
                 patch.object(fg, 'LOGIN_LOCK', Path(directory) / 'stable.lock'), \
                 patch.object(fg, 'wait_tunnel', side_effect=[None, ConnectionError(), None]), patch.object(fg, 'probe') as probe, \
                 patch.object(fg.time, 'sleep') as sleep, patch.object(fg, 'stop'), \
                 patch.object(fg.subprocess, 'Popen', side_effect=spawn), \
                 patch.object(fg, 'supervise', side_effect=supervise):
                with self.assertRaises(KeyboardInterrupt): fg.run(600, resident=True)
            self.assertEqual(len(commands), 4)  # two ready tunnels, one failed tunnel, one worker
            self.assertEqual(sum('--resident' in command for command in commands), 1)
            forward1 = commands[0][commands[0].index('-L') + 1]
            forward2 = commands[3][commands[3].index('-L') + 1]
            self.assertEqual(forward1, forward2)
            self.assertTrue(forward2.endswith(':127.0.0.1:18002'))
            self.assertEqual(probe.call_count, 2)
            sleep.assert_called_once_with(5)

    def test_auth_probe_rejection_never_retries_or_starts_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / 'Cookies'; cookie.write_bytes(b'SQLite format 3\x00')
            with patch.dict(os.environ, {'DRAIN_TOKEN': 'synthetic'}), \
                 patch.object(fg, 'COOKIE_DB', cookie), \
                 patch.object(fg.tempfile, 'gettempdir', return_value=directory), \
                 patch.object(fg, 'LOGIN_LOCK', Path(directory) / 'stable.lock'), \
                 patch.object(fg, 'wait_tunnel'), \
                 patch.object(fg, 'probe', side_effect=fg.RunError('private-auth-validation-failed')), \
                 patch.object(fg.time, 'sleep') as sleep, patch.object(fg, 'stop'), \
                 patch.object(fg.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(fg.RunError, 'private-auth-validation-failed'):
                    fg.run(600, resident=True)
            spawn.assert_called_once()
            sleep.assert_not_called()


class MacAccessTests(unittest.TestCase):
    def test_no_ui_keychain_denial_restores_policy_without_fallback(self):
        security = Mock()
        security.SecKeychainGetUserInteractionAllowed.return_value = 0
        security.SecKeychainSetUserInteractionAllowed.return_value = 0
        security.SecKeychainFindGenericPassword.return_value = -25308
        with patch.object(mac_access.ctypes, 'CDLL', return_value=security), \
             self.assertRaisesRegex(RuntimeError, 'keychain-existing-access-required'):
            mac_access.keyring_password('Chrome', Mock())
        self.assertEqual(security.SecKeychainSetUserInteractionAllowed.call_args_list, [call(False), call(0)])
        security.SecKeychainFindGenericPassword.assert_called_once()
        security.SecKeychainItemFreeContent.assert_not_called()

    def test_unavailable_no_ui_api_never_reads_item(self):
        security = Mock()
        security.SecKeychainGetUserInteractionAllowed.return_value = -1
        with patch.object(mac_access.ctypes, 'CDLL', return_value=security), self.assertRaises(RuntimeError):
            mac_access.keyring_password('Chrome', Mock())
        security.SecKeychainFindGenericPassword.assert_not_called()

    def test_mac_extraction_uses_no_ui_helper_and_restores_upstream(self):
        original = Mock(side_effect=AssertionError('prompting helper must never run'))
        cookies = SimpleNamespace(_get_mac_keyring_password=original)
        def extract(*args, **kwargs):
            self.assertEqual(args, ('chrome',))
            self.assertEqual(kwargs['profile'], 'Profile 1')
            self.assertIsInstance(kwargs['logger'], c.CookieLogger)
            self.assertEqual(cookies._get_mac_keyring_password('Chrome', Mock()), b'synthetic')
            return []
        cookies.extract_cookies_from_browser = Mock(side_effect=extract)
        with patch.dict('sys.modules', {'yt_dlp': SimpleNamespace(cookies=cookies)}), \
             patch.object(c.sys, 'platform', 'darwin'), \
             patch.object(mac_access, 'keyring_password', return_value=b'synthetic') as native:
            with self.assertRaisesRegex(c.ConnectorError, 'session-expired'):
                c.instagram_session('Profile 1')
        native.assert_called_once()
        original.assert_not_called()
        self.assertIs(cookies._get_mac_keyring_password, original)

    def test_missing_upstream_adapter_fails_closed(self):
        cookies = SimpleNamespace(extract_cookies_from_browser=Mock())
        with patch.dict('sys.modules', {'yt_dlp': SimpleNamespace(cookies=cookies)}), \
             patch.object(c.sys, 'platform', 'darwin'), \
             self.assertRaisesRegex(c.ConnectorError, '^keychain-access-required$'):
            c.instagram_session('Profile 1')
        cookies.extract_cookies_from_browser.assert_not_called()

    def test_opt_in_uses_existing_helper_once_and_filters_cookies(self):
        original = Mock(return_value=b'synthetic-key')
        cookies = SimpleNamespace(_get_mac_keyring_password=original)
        def extract(*args, **kwargs):
            self.assertEqual(cookies._get_mac_keyring_password('Chrome', kwargs['logger']), b'synthetic-key')
            return [requests.cookies.create_cookie('sessionid', 'synthetic', domain='.instagram.com'),
                    requests.cookies.create_cookie('other', 'synthetic', domain='fixture.invalid')]
        cookies.extract_cookies_from_browser = Mock(side_effect=extract)
        with patch.dict('sys.modules', {'yt_dlp': SimpleNamespace(cookies=cookies)}), \
             patch.object(c.sys, 'platform', 'darwin'), patch.object(mac_access, 'keyring_password') as native:
            session = c.instagram_session('Profile 1', allow_keychain_prompt=True)
        self.assertEqual(session.cookies.get_dict(), {'sessionid': 'synthetic'})
        session.close()
        original.assert_called_once()
        native.assert_not_called()
        cookies.extract_cookies_from_browser.assert_called_once()
        self.assertIs(cookies._get_mac_keyring_password, original)

    def test_helper_denials_do_not_leak_retry_or_fall_back_and_restore_patch(self):
        for allow_prompt in (False, True):
            for failure in (None, RuntimeError('synthetic-secret')):
                original = Mock(return_value=None, side_effect=failure)
                cookies = SimpleNamespace(_get_mac_keyring_password=original)
                def extract(*args, **kwargs):
                    kwargs['logger'].warning('synthetic-secret')
                    return cookies._get_mac_keyring_password('Chrome', kwargs['logger'])
                cookies.extract_cookies_from_browser = Mock(side_effect=extract)
                with patch.dict('sys.modules', {'yt_dlp': SimpleNamespace(cookies=cookies)}), \
                     patch.object(c.sys, 'platform', 'darwin'), \
                     patch.object(mac_access, 'keyring_password', side_effect=failure,
                                  return_value=None) as native, \
                     patch('sys.stdout', new_callable=io.StringIO) as output, \
                     patch('sys.stderr', new_callable=io.StringIO) as error, \
                     self.assertRaisesRegex(c.ConnectorError, '^keychain-access-required$'):
                    c.instagram_session('Profile 1', allow_keychain_prompt=allow_prompt)
                self.assertEqual(original.call_count, int(allow_prompt))
                self.assertEqual(native.call_count, int(not allow_prompt))
                self.assertEqual(output.getvalue() + error.getvalue(), '')
                cookies.extract_cookies_from_browser.assert_called_once()
                self.assertIs(cookies._get_mac_keyring_password, original)

    def test_synthetic_key_is_freed_and_prior_policy_restored(self):
        security = Mock()
        buffer = ctypes.create_string_buffer(b'synthetic-only')
        def get_state(state):
            state._obj.value = 1
            return 0
        def find(*args):
            self.assertEqual(args[2], b'Chrome Safe Storage')
            self.assertEqual(args[4], b'Chrome')
            args[5]._obj.value = len(b'synthetic-only')
            args[6]._obj.value = ctypes.addressof(buffer)
            return 0
        security.SecKeychainGetUserInteractionAllowed.side_effect = get_state
        security.SecKeychainSetUserInteractionAllowed.return_value = 0
        security.SecKeychainFindGenericPassword.side_effect = find
        with patch.object(mac_access.ctypes, 'CDLL', return_value=security):
            self.assertEqual(mac_access.keyring_password('Chrome', Mock()), b'synthetic-only')
        security.SecKeychainItemFreeContent.assert_called_once()
        self.assertEqual(security.SecKeychainSetUserInteractionAllowed.call_args_list, [call(False), call(1)])


if __name__ == '__main__':
    unittest.main()
