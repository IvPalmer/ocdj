"""Hermetic tests for the ytfetch app — no real yt-dlp, no real network/disk.

subprocess.run is mocked everywhere; the pipeline ingest is stubbed. get_config
is redirected to a temp dir so nothing touches the real download root.
"""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from organize.models import PipelineItem
from ytfetch import tasks as ytfetch_tasks
from ytfetch.models import FetchJob


def _proc(returncode=0, stdout='', stderr=''):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class UrlValidationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_rejects_non_youtube_url(self):
        with patch('ytfetch.views.task_fetch') as mock_task:
            resp = self.client.post(
                '/api/ytfetch/fetch/', {'url': 'https://example.com/track'}, format='json'
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(FetchJob.objects.count(), 0)
        mock_task.assert_not_called()

    def test_accepts_youtube_url_and_enqueues(self):
        with patch('ytfetch.views.task_fetch') as mock_task:
            resp = self.client.post(
                '/api/ytfetch/fetch/',
                {'url': 'https://www.youtube.com/watch?v=abc123'},
                format='json',
            )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(FetchJob.objects.count(), 1)
        job = FetchJob.objects.get()
        self.assertEqual(job.status, 'queued')
        mock_task.assert_called_once_with(job.id)

    def test_accepts_youtu_be_and_shorts(self):
        with patch('ytfetch.views.task_fetch'):
            for url in (
                'https://youtu.be/abc123',
                'https://youtube.com/shorts/xyz789',
            ):
                resp = self.client.post(
                    '/api/ytfetch/fetch/', {'url': url}, format='json'
                )
                self.assertEqual(resp.status_code, 202, url)


class TaskSuccessTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.yt_dir = os.path.join(self.root, '01_downloaded', 'YouTube')
        os.makedirs(self.yt_dir)
        self.filepath = os.path.join(self.yt_dir, 'Uploader - Title [abc123].wav')
        open(self.filepath, 'w').close()  # real file so os.path.exists is True

    def _config(self, key, *a, **k):
        return {'SOULSEEK_DOWNLOAD_ROOT': self.root}.get(key, '')

    def test_success_links_item_and_processes(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        item = PipelineItem.objects.create(
            original_filename='Uploader - Title [abc123].wav',
            current_path=self.filepath,
            stage='downloaded',
        )

        meta_out = 'abc123\tUploader\tTitle'
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(0, meta_out), _proc(0, self.filepath)]), \
             patch('organize.services.pipeline.scan_completed_downloads') as mock_scan, \
             patch('organize.services.pipeline.process_pipeline_item') as mock_proc:
            ytfetch_tasks.run_fetch_job(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, 'downloaded')
        self.assertEqual(job.downloaded_path, self.filepath)
        self.assertEqual(job.video_id, 'abc123')
        self.assertEqual(job.uploader, 'Uploader')
        self.assertEqual(job.title, 'Title')
        self.assertEqual(job.pipeline_item_id, item.id)
        mock_scan.assert_called_once()
        mock_proc.assert_called_once_with(item.id)

    def test_download_uses_highest_quality_flags(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        PipelineItem.objects.create(
            original_filename='Uploader - Title [abc123].wav',
            current_path=self.filepath,
            stage='downloaded',
        )
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), _proc(0, self.filepath)]) as mock_run, \
             patch('organize.services.pipeline.scan_completed_downloads'), \
             patch('organize.services.pipeline.process_pipeline_item'):
            ytfetch_tasks.run_fetch_job(job.id)

        # Second call is the download; assert the operator's exact flag set.
        download_argv = mock_run.call_args_list[1].args[0]
        for token in (
            '--js-runtimes', 'node', '--no-playlist',
            '--remote-components', 'ejs:github',
            '-f', 'bestaudio/best',
            '--extract-audio', '--audio-format', 'wav', '--audio-quality', '0',
            '--print', 'after_move:filepath', '--no-progress', '--',
        ):
            self.assertIn(token, download_argv)
        # -f value must immediately follow the -f flag.
        self.assertEqual(download_argv[download_argv.index('-f') + 1], 'bestaudio/best')
        # Options must be terminated with `--` immediately before the URL.
        self.assertEqual(download_argv[-2], '--')
        self.assertEqual(download_argv[-1], job.url)
        # Output template: uploader default + unique [id] suffix.
        output_tmpl = download_argv[download_argv.index('--output') + 1]
        self.assertIn('[%(id)s]', output_tmpl)
        self.assertIn('%(artist,creator,uploader|YouTube)s', output_tmpl)

    def test_cookie_file_seeds_persistent_self_refreshing_copy(self):
        # The read-only secret is the seed; the task copies it once into a
        # writable state dir and points yt-dlp at that live copy, which yt-dlp
        # keeps fresh via YouTube's rotation. The live copy must PERSIST (not be
        # deleted) so the refresh survives to the next run, and must never be
        # the read-only seed itself.
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        seed = os.path.join(self.root, 'seed_cookies.txt')
        with open(seed, 'w') as fh:
            fh.write('# Netscape HTTP Cookie File\nSEED_COOKIE_CONTENT\n')
        state_dir = os.path.join(self.root, 'ytcookies')

        def config(key, *a, **k):
            return {
                'SOULSEEK_DOWNLOAD_ROOT': self.root,
                'YOUTUBE_COOKIES': seed,
                'YOUTUBE_COOKIES_FROM_BROWSER': 'chrome',
            }.get(key, '')

        seen = {}

        def run_side_effect(argv, *a, **k):
            self.assertIn('--cookies', argv)
            path = argv[argv.index('--cookies') + 1]
            self.assertNotEqual(path, seed)  # live copy, never the read-only seed
            self.assertEqual(os.path.dirname(path), state_dir)
            self.assertNotIn('--cookies-from-browser', argv)
            with open(path) as fh:
                self.assertEqual(fh.read(), open(seed).read())  # seeded from source
            seen['path'] = path
            seen['calls'] = seen.get('calls', 0) + 1
            return _proc(0, '') if seen['calls'] == 1 else _proc(0, self.filepath)

        with patch.object(ytfetch_tasks, 'get_config', side_effect=config), \
             patch.object(ytfetch_tasks, 'COOKIE_STATE_DIR', state_dir), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=run_side_effect), \
             patch('organize.services.pipeline.scan_completed_downloads'), \
             patch('organize.services.pipeline.process_pipeline_item'):
            ytfetch_tasks.run_fetch_job(job.id)

        self.assertIn('path', seen)
        self.assertTrue(os.path.exists(seen['path']))  # persists for self-refresh

    def test_browser_cookie_auth_is_used_when_cookie_file_is_empty(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123')

        def config(key, *a, **k):
            return {
                'SOULSEEK_DOWNLOAD_ROOT': self.root,
                'YOUTUBE_COOKIES_FROM_BROWSER': 'chrome:Profile 1',
            }.get(key, '')

        with patch.object(ytfetch_tasks, 'get_config', side_effect=config), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), _proc(0, self.filepath)]) as mock_run, \
             patch('organize.services.pipeline.scan_completed_downloads'), \
             patch('organize.services.pipeline.process_pipeline_item'):
            ytfetch_tasks.run_fetch_job(job.id)

        for call in mock_run.call_args_list:
            argv = call.args[0]
            self.assertIn('--cookies-from-browser', argv)
            self.assertEqual(
                argv[argv.index('--cookies-from-browser') + 1],
                'chrome:Profile 1',
            )

    def test_links_item_created_by_scan(self):
        """The normal live path: no PipelineItem exists until the scan runs."""
        job = FetchJob.objects.create(url='https://youtu.be/abc123')

        def fake_scan():
            PipelineItem.objects.create(
                original_filename=os.path.basename(self.filepath),
                current_path=self.filepath,
                stage='downloaded',
            )
            return 1

        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), _proc(0, self.filepath)]), \
             patch('organize.services.pipeline.scan_completed_downloads',
                   side_effect=fake_scan), \
             patch('organize.services.pipeline.process_pipeline_item') as mock_proc:
            ytfetch_tasks.run_fetch_job(job.id)

        job.refresh_from_db()
        item = PipelineItem.objects.get()
        self.assertEqual(job.status, 'downloaded')
        self.assertEqual(job.pipeline_item_id, item.id)
        mock_proc.assert_called_once_with(item.id)

    def test_duplicate_enqueue_is_noop_when_already_downloaded(self):
        job = FetchJob.objects.create(
            url='https://youtu.be/abc123', status='downloaded',
            downloaded_path=self.filepath,
        )
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.object(ytfetch_tasks.subprocess, 'run') as mock_run:
            ytfetch_tasks.run_fetch_job(job.id)

        mock_run.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, 'downloaded')
        self.assertEqual(job.downloaded_path, self.filepath)


class TaskFailureTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def _config(self, key, *a, **k):
        return {'SOULSEEK_DOWNLOAD_ROOT': self.root}.get(key, '')

    def test_failure_stores_stderr_tail_and_bot_hint(self):
        # The bot-hint _fail path only applies when the Mac fallback is OFF;
        # with it on, a bot-check routes to needs_local (covered elsewhere).
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        stderr = 'ERROR: Sign in to confirm you’re not a bot. Use --cookies.'
        env = {k: v for k, v in os.environ.items() if k != 'YTFETCH_LOCAL_FALLBACK'}
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.dict(os.environ, env, clear=True), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), _proc(1, '', stderr)]):
            ytfetch_tasks.run_fetch_job(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertIn('bot-check', job.error_message)
        self.assertIn('Use --cookies', job.error_message)

    def test_failure_when_no_output_file(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), _proc(0, '/nonexistent/x.wav')]):
            ytfetch_tasks.run_fetch_job(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')


class RetryTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_retry_only_from_failed(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123', status='downloaded')
        with patch('ytfetch.views.task_fetch') as mock_task:
            resp = self.client.post(f'/api/ytfetch/jobs/{job.id}/retry/')
        self.assertEqual(resp.status_code, 400)
        mock_task.assert_not_called()

    def test_retry_requeues_failed_job(self):
        job = FetchJob.objects.create(
            url='https://youtu.be/abc123', status='failed', error_message='boom'
        )
        with patch('ytfetch.views.task_fetch') as mock_task:
            resp = self.client.post(f'/api/ytfetch/jobs/{job.id}/retry/')
        self.assertEqual(resp.status_code, 202)
        job.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.error_message, '')
        mock_task.assert_called_once_with(job.id)


class DeleteTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_delete_removes_row_without_touching_files(self):
        job = FetchJob.objects.create(
            url='https://youtu.be/abc123', status='downloaded',
            downloaded_path='/music/01_downloaded/YouTube/x.wav',
        )
        with patch('os.remove') as mock_remove:
            resp = self.client.delete(f'/api/ytfetch/jobs/{job.id}/')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(FetchJob.objects.filter(id=job.id).exists())
        mock_remove.assert_not_called()

    def test_delete_blocked_while_queued_or_fetching(self):
        for job_status in ('queued', 'fetching'):
            job = FetchJob.objects.create(
                url='https://youtu.be/abc123', status=job_status,
            )
            resp = self.client.delete(f'/api/ytfetch/jobs/{job.id}/')
            self.assertEqual(resp.status_code, 400, job_status)
            self.assertTrue(FetchJob.objects.filter(id=job.id).exists())


class JobsListTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_lists_recent_jobs(self):
        for i in range(3):
            FetchJob.objects.create(url=f'https://youtu.be/v{i}')
        resp = self.client.get('/api/ytfetch/jobs/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 3)
        # Ordering -id: newest first.
        self.assertGreater(resp.data['results'][0]['id'], resp.data['results'][1]['id'])


class YtdlpPotAndFallbackTestCase(TestCase):
    """PO-token args + Mac-tunnel bot-check fallback."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.yt_dir = os.path.join(self.root, '01_downloaded', 'YouTube')
        os.makedirs(self.yt_dir, exist_ok=True)
        self.filepath = os.path.join(self.yt_dir, 'U - T [abc123].wav')
        with open(self.filepath, 'wb') as fh:
            fh.write(b'audio')

    def tearDown(self):
        self.tmp.cleanup()

    def _config(self, key, *a, **k):
        return {'SOULSEEK_DOWNLOAD_ROOT': self.root}.get(key, '')

    def test_pot_extractor_arg_present_when_provider_configured(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.dict(os.environ, {'YOUTUBE_POT_BASE_URL': 'http://bgutil-pot:4416'}, clear=False), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), _proc(0, self.filepath)]) as mock_run, \
             patch('organize.services.pipeline.scan_completed_downloads'), \
             patch('organize.services.pipeline.process_pipeline_item'):
            ytfetch_tasks.run_fetch_job(job.id)
        argv = mock_run.call_args_list[1].args[0]
        self.assertIn('--extractor-args', argv)
        self.assertEqual(
            argv[argv.index('--extractor-args') + 1],
            'youtubepot-bgutilhttp:base_url=http://bgutil-pot:4416',
        )

    def test_bot_check_retries_through_mac_proxy(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        botcheck = _proc(1, '', 'ERROR: Sign in to confirm you’re not a bot.')
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.dict(os.environ, {'YOUTUBE_MAC_PROXY': 'socks5://172.22.0.1:1080'}, clear=False), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), botcheck, _proc(0, self.filepath)]) as mock_run, \
             patch('organize.services.pipeline.scan_completed_downloads'), \
             patch('organize.services.pipeline.process_pipeline_item'):
            ytfetch_tasks.run_fetch_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'downloaded')
        # 3 calls: meta, primary (bot-checked), fallback via proxy.
        self.assertEqual(mock_run.call_count, 3)
        fallback_argv = mock_run.call_args_list[2].args[0]
        self.assertIn('--proxy', fallback_argv)
        self.assertEqual(
            fallback_argv[fallback_argv.index('--proxy') + 1],
            'socks5://172.22.0.1:1080',
        )

    def test_no_fallback_when_mac_proxy_unset(self):
        # Neither the Mac proxy retry nor the needs_local routing: a bot-check
        # with both YOUTUBE_MAC_PROXY and YTFETCH_LOCAL_FALLBACK unset fails.
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        botcheck = _proc(1, '', 'ERROR: Sign in to confirm you’re not a bot.')
        env = {k: v for k, v in os.environ.items()
               if k not in ('YOUTUBE_MAC_PROXY', 'YTFETCH_LOCAL_FALLBACK')}
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.dict(os.environ, env, clear=True), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), botcheck]) as mock_run:
            ytfetch_tasks.run_fetch_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(mock_run.call_count, 2)  # meta + primary only, no fallback


class LocalFallbackRoutingTests(TestCase):
    """Bot-check → 'needs_local' parking when YTFETCH_LOCAL_FALLBACK is set."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def _config(self, key, *a, **k):
        return {'SOULSEEK_DOWNLOAD_ROOT': self.root}.get(key, '')

    def _env(self, **extra):
        # Start from a clean slate so an ambient YTFETCH_LOCAL_FALLBACK /
        # YOUTUBE_MAC_PROXY in the runner's environment can't skew the test.
        base = {
            k: v for k, v in os.environ.items()
            if k not in ('YTFETCH_LOCAL_FALLBACK', 'YOUTUBE_MAC_PROXY')
        }
        base.update(extra)
        return base

    def test_bot_check_routes_to_needs_local_when_flag_set(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        botcheck = _proc(1, '', 'ERROR: Sign in to confirm you’re not a bot.')
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.dict(os.environ, self._env(YTFETCH_LOCAL_FALLBACK='1'), clear=True), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), botcheck]):
            ytfetch_tasks.run_fetch_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'needs_local')
        self.assertIn('Mac local download', job.error_message)

    def test_bot_check_still_fails_without_flag(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        botcheck = _proc(1, '', 'ERROR: Sign in to confirm you’re not a bot.')
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.dict(os.environ, self._env(), clear=True), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), botcheck]):
            ytfetch_tasks.run_fetch_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')

    def test_non_bot_check_failure_still_fails_with_flag(self):
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        other = _proc(1, '', 'ERROR: Video unavailable')
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
             patch.dict(os.environ, self._env(YTFETCH_LOCAL_FALLBACK='1'), clear=True), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=[_proc(1, ''), other]):
            ytfetch_tasks.run_fetch_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')


class IngestAndProcessTests(TestCase):
    """The extracted shared ingest helper links a scan-created item + processes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.yt_dir = os.path.join(self.root, '01_downloaded', 'YouTube')
        os.makedirs(self.yt_dir)
        self.filepath = os.path.join(self.yt_dir, 'U - T [abc123].wav')
        open(self.filepath, 'w').close()

    def test_links_scan_created_item_and_processes(self):
        job = FetchJob.objects.create(
            url='https://youtu.be/abc123', status='downloaded',
            downloaded_path=self.filepath,
        )

        def fake_scan():
            PipelineItem.objects.create(
                original_filename=os.path.basename(self.filepath),
                current_path=self.filepath,
                stage='downloaded',
            )
            return 1

        with patch('organize.services.pipeline.scan_completed_downloads',
                   side_effect=fake_scan), \
             patch('organize.services.pipeline.process_pipeline_item') as mock_proc:
            ytfetch_tasks.ingest_and_process(job, self.filepath)

        job.refresh_from_db()
        item = PipelineItem.objects.get()
        self.assertEqual(job.pipeline_item_id, item.id)
        mock_proc.assert_called_once_with(item.id)


class PendingLocalEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_requires_bearer(self):
        with patch.dict(os.environ, {'KICK_TOKEN': 'secret'}, clear=False):
            resp = self.client.get('/api/ytfetch/pending-local/')
        self.assertEqual(resp.status_code, 401)

    def test_lists_needs_local_oldest_first(self):
        a = FetchJob.objects.create(url='https://youtu.be/a', status='needs_local',
                                    title='A')
        b = FetchJob.objects.create(url='https://youtu.be/b', status='needs_local',
                                    title='B')
        FetchJob.objects.create(url='https://youtu.be/c', status='failed')
        with patch.dict(os.environ, {'KICK_TOKEN': 'secret'}, clear=False):
            resp = self.client.get(
                '/api/ytfetch/pending-local/', HTTP_AUTHORIZATION='Bearer secret'
            )
        self.assertEqual(resp.status_code, 200)
        ids = [j['id'] for j in resp.data['jobs']]
        self.assertEqual(ids, [a.id, b.id])  # oldest first, failed excluded
        self.assertEqual(resp.data['jobs'][0]['title'], 'A')


class DeliverLocalEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def _config(self, key, *a, **k):
        return {'SOULSEEK_DOWNLOAD_ROOT': self.root}.get(key, '')

    def test_requires_bearer(self):
        job = FetchJob.objects.create(url='https://youtu.be/a', status='needs_local')
        upload = SimpleUploadedFile('x.wav', b'audio', content_type='audio/wav')
        with patch.dict(os.environ, {'KICK_TOKEN': 'secret'}, clear=False):
            resp = self.client.post(
                f'/api/ytfetch/{job.id}/deliver-local/',
                {'file': upload}, format='multipart',
            )
        self.assertEqual(resp.status_code, 401)

    def test_delivers_writes_file_and_ingests(self):
        job = FetchJob.objects.create(url='https://youtu.be/a', status='needs_local',
                                      video_id='abc123')
        upload = SimpleUploadedFile('Track [abc123].wav', b'audio',
                                    content_type='audio/wav')
        with patch.dict(os.environ, {'KICK_TOKEN': 'secret'}, clear=False), \
             patch('ytfetch.views.get_config', side_effect=self._config), \
             patch('organize.services.pipeline.scan_completed_downloads') as mock_scan, \
             patch('organize.services.pipeline.process_pipeline_item') as mock_proc:
            # Pre-create the item the scan would have produced so the ingest links it.
            dest = os.path.join(self.root, '01_downloaded', 'YouTube',
                                'Track [abc123].wav')

            def fake_scan():
                PipelineItem.objects.create(
                    original_filename='Track [abc123].wav',
                    current_path=dest, stage='downloaded',
                )
                return 1
            mock_scan.side_effect = fake_scan
            resp = self.client.post(
                f'/api/ytfetch/{job.id}/deliver-local/',
                {'file': upload}, format='multipart',
                HTTP_AUTHORIZATION='Bearer secret',
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(os.path.exists(dest))
        job.refresh_from_db()
        self.assertEqual(job.status, 'downloaded')
        self.assertEqual(job.downloaded_path, dest)
        item = PipelineItem.objects.get()
        self.assertEqual(job.pipeline_item_id, item.id)
        mock_scan.assert_called_once()
        mock_proc.assert_called_once_with(item.id)

    def test_wrong_status_returns_409(self):
        job = FetchJob.objects.create(url='https://youtu.be/a', status='downloaded')
        upload = SimpleUploadedFile('x.wav', b'audio', content_type='audio/wav')
        with patch.dict(os.environ, {'KICK_TOKEN': 'secret'}, clear=False), \
             patch('ytfetch.views.get_config', side_effect=self._config):
            resp = self.client.post(
                f'/api/ytfetch/{job.id}/deliver-local/',
                {'file': upload}, format='multipart',
                HTTP_AUTHORIZATION='Bearer secret',
            )
        self.assertEqual(resp.status_code, 409)

    def test_missing_file_returns_400(self):
        job = FetchJob.objects.create(url='https://youtu.be/a', status='needs_local')
        with patch.dict(os.environ, {'KICK_TOKEN': 'secret'}, clear=False), \
             patch('ytfetch.views.get_config', side_effect=self._config):
            resp = self.client.post(
                f'/api/ytfetch/{job.id}/deliver-local/',
                {}, format='multipart',
                HTTP_AUTHORIZATION='Bearer secret',
            )
        self.assertEqual(resp.status_code, 400)
