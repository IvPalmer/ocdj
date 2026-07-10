"""Hermetic tests for the ytfetch app — no real yt-dlp, no real network/disk.

subprocess.run is mocked everywhere; the pipeline ingest is stubbed. get_config
is redirected to a temp dir so nothing touches the real download root.
"""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_cookie_file_auth_uses_writable_copy_and_cleans_up(self):
        # yt-dlp rewrites the cookie jar on exit; the prod secret is mounted
        # read-only, so the task must pass a writable COPY and delete it after.
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        src = os.path.join(self.root, 'youtube_cookies.txt')
        with open(src, 'w') as fh:
            fh.write('# Netscape HTTP Cookie File\nSOURCE_COOKIE_CONTENT\n')

        def config(key, *a, **k):
            return {
                'SOULSEEK_DOWNLOAD_ROOT': self.root,
                'YOUTUBE_COOKIES': src,
                'YOUTUBE_COOKIES_FROM_BROWSER': 'chrome',
            }.get(key, '')

        seen = {}

        def run_side_effect(argv, *a, **k):
            self.assertIn('--cookies', argv)
            path = argv[argv.index('--cookies') + 1]
            self.assertNotEqual(path, src)  # a copy, never the read-only source
            self.assertNotIn('--cookies-from-browser', argv)
            with open(path) as fh:  # the copy exists and matches the source
                self.assertEqual(fh.read(), open(src).read())
            seen['path'] = path
            # First call = metadata pre-pass (returns no tab-separated meta so
            # it's skipped); the download call returns the final filepath.
            seen['calls'] = seen.get('calls', 0) + 1
            return _proc(0, '') if seen['calls'] == 1 else _proc(0, self.filepath)

        with patch.object(ytfetch_tasks, 'get_config', side_effect=config), \
             patch.object(ytfetch_tasks.subprocess, 'run',
                          side_effect=run_side_effect), \
             patch('organize.services.pipeline.scan_completed_downloads'), \
             patch('organize.services.pipeline.process_pipeline_item'):
            ytfetch_tasks.run_fetch_job(job.id)

        self.assertIn('path', seen)
        self.assertFalse(os.path.exists(seen['path']))  # temp copy cleaned up

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
        job = FetchJob.objects.create(url='https://youtu.be/abc123')
        stderr = 'ERROR: Sign in to confirm you’re not a bot. Use --cookies.'
        with patch.object(ytfetch_tasks, 'get_config', side_effect=self._config), \
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
