"""Safety tests for strict TraxDB destination handling."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from traxdb.models import ScrapedFolder, TraxDBOperation
from traxdb.services import downloader


class RunDownloadDestinationTestCase(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        closer = patch.object(downloader.db.connections, 'close_all')
        closer.start()
        self.addCleanup(closer.stop)

    def test_existing_destination_is_skipped_without_listing_files(self):
        folder = ScrapedFolder.objects.create(
            folder_id='existing-list',
            pixeldrain_url='https://pixeldrain.com/l/existing-list',
            inferred_date='2026-05-01',
            download_status='pending',
        )
        Path(self.tmpdir.name, '2026-05-01').mkdir()
        op = TraxDBOperation.objects.create(op_type='download', status='pending')
        values = {
            'TRAXDB_ROOT': self.tmpdir.name,
            'PIXELDRAIN_API_KEY': 'test-key',
        }

        with patch.object(downloader, 'get_config', side_effect=values.get), \
             patch.object(downloader, 'PixeldrainClient') as mock_client:
            downloader.run_download(op.id)

        op.refresh_from_db()
        folder.refresh_from_db()
        self.assertEqual(op.status, 'completed')
        self.assertEqual(op.summary['lists_skipped_existing_directory'], 1)
        self.assertEqual(folder.download_status, 'skipped')
        mock_client.return_value.iter_list_files.assert_not_called()
