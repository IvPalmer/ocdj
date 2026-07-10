"""run_sync fetch-mode branching tests (fetchers mocked, no HTTP)."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from traxdb.models import ScrapedFolder, TraxDBOperation
from traxdb.services import blogger_api
from traxdb.services import scraper


class RunSyncFetchModeTestCase(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        # run_sync's finally block closes all DB connections, which would break
        # the TestCase transaction — neutralize it for the duration of the test.
        closer = patch.object(scraper.db.connections, 'close_all')
        closer.start()
        self.addCleanup(closer.stop)
        self.op = TraxDBOperation.objects.create(op_type='sync', status='pending')

    def _config(self, fetch_mode):
        values = {
            'TRAXDB_ROOT': self.tmpdir.name,
            'TRAXDB_START_URL': 'https://traxdb2.blogspot.com',
            'PIXELDRAIN_API_KEY': '',
            'TRAXDB_COOKIES': '',
            'TRAXDB_FETCH_MODE': fetch_mode,
        }
        return lambda key, *a, **k: values.get(key, '')

    def test_api_mode_uses_blogger_api(self):
        with patch.object(scraper, 'get_config', side_effect=self._config('api')), \
             patch.object(blogger_api, 'iter_blog_links', return_value=[]) as mock_api, \
             patch.object(scraper, 'scrape_blog_links') as mock_cookie:
            scraper.run_sync(self.op.id)

        self.op.refresh_from_db()
        self.assertEqual(self.op.status, 'completed')
        mock_api.assert_called_once()
        mock_cookie.assert_not_called()

    def test_cookies_mode_uses_cookie_scraper(self):
        with patch.object(scraper, 'get_config', side_effect=self._config('cookies')), \
             patch.object(blogger_api, 'iter_blog_links') as mock_api, \
             patch.object(scraper, 'scrape_blog_links', return_value=[]) as mock_cookie, \
             patch.object(scraper, '_make_session') as mock_session:
            scraper.run_sync(self.op.id)

        self.op.refresh_from_db()
        self.assertEqual(self.op.status, 'completed')
        mock_cookie.assert_called_once()
        mock_session.assert_called_once()
        mock_api.assert_not_called()

    def test_invalid_mode_fails_operation(self):
        with patch.object(scraper, 'get_config', side_effect=self._config('coookies')), \
             patch.object(blogger_api, 'iter_blog_links') as mock_api, \
             patch.object(scraper, 'scrape_blog_links') as mock_cookie:
            scraper.run_sync(self.op.id)

        self.op.refresh_from_db()
        self.assertEqual(self.op.status, 'failed')
        self.assertEqual(self.op.error_message, 'invalid TRAXDB_FETCH_MODE: coookies')
        mock_api.assert_not_called()
        mock_cookie.assert_not_called()

    def test_existing_destination_is_not_queued_for_download(self):
        Path(self.tmpdir.name, '_inbox', 'existing').mkdir(parents=True)
        link = scraper.TraxDBLink(
            pixeldrain_url='https://pixeldrain.com/l/existing',
            list_id='existing',
            source_url='https://traxdb2.blogspot.com/post',
            inferred_date=None,
        )

        with patch.object(scraper, 'get_config', side_effect=self._config('api')), \
             patch.object(blogger_api, 'iter_blog_links', return_value=[link]):
            scraper.run_sync(self.op.id)

        self.op.refresh_from_db()
        self.assertEqual(self.op.status, 'completed')
        self.assertEqual(self.op.summary['links_new_count'], 0)
        self.assertEqual(self.op.summary['links_skipped_by_existing_directory'], 1)
        self.assertFalse(ScrapedFolder.objects.filter(folder_id='existing').exists())

    def test_empty_date_destination_remains_eligible(self):
        Path(self.tmpdir.name, '2026-05-01').mkdir(parents=True)
        link = scraper.TraxDBLink(
            pixeldrain_url='https://pixeldrain.com/l/empty-date',
            list_id='empty-date',
            source_url='https://traxdb2.blogspot.com/post',
            inferred_date='2026-05-01',
        )

        with patch.object(scraper, 'get_config', side_effect=self._config('api')), \
             patch.object(blogger_api, 'iter_blog_links', return_value=[link]):
            scraper.run_sync(self.op.id)

        self.op.refresh_from_db()
        self.assertEqual(self.op.summary['links_new_count'], 1)
        self.assertTrue(ScrapedFolder.objects.filter(folder_id='empty-date').exists())
