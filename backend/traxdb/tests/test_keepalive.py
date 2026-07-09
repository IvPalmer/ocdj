"""Pixeldrain keepalive periodic task — hermetic (all HTTP mocked)."""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from traxdb.models import ScrapedFolder
from traxdb.services.pixeldrain import PixeldrainError
from traxdb import tasks


class PixeldrainKeepaliveTestCase(TestCase):
    def _run(self):
        # Huey task objects wrap the function; call_local runs it inline.
        return tasks.task_pixeldrain_keepalive.call_local()

    @patch('traxdb.services.pixeldrain.PixeldrainClient.get_list')
    @patch('core.services.config.get_config', return_value='key-123')
    def test_keepalive_uses_latest_folder_list(self, _cfg, get_list):
        ScrapedFolder.objects.create(
            folder_id='abc', title='x', url='u',
            pixeldrain_url='https://pixeldrain.com/l/OLD111',
        )
        ScrapedFolder.objects.create(
            folder_id='def', title='y', url='u2',
            pixeldrain_url='https://pixeldrain.com/l/NEW222',
        )
        get_list.return_value = {'files': [{'id': 'f1'}]}
        self._run()
        get_list.assert_called_once_with('NEW222')

    @patch('traxdb.services.pixeldrain.PixeldrainClient.get_list')
    @patch('core.services.config.get_config', return_value='key-123')
    def test_keepalive_401_logs_and_raises(self, _cfg, get_list):
        get_list.side_effect = PixeldrainError(
            'Pixeldrain list fetch failed (401): authentication_failed'
        )
        ScrapedFolder.objects.create(
            folder_id='abc', title='x', url='u',
            pixeldrain_url='https://pixeldrain.com/l/DEAD00',
        )
        with self.assertLogs('traxdb.tasks', level='ERROR') as logs:
            with self.assertRaises(PixeldrainError):
                self._run()
        self.assertTrue(any('API key is dead or revoked' in m for m in logs.output))

    @patch('core.services.config.get_config', return_value='')
    def test_keepalive_skips_without_key(self, _cfg):
        # No key configured: warn and return, no exception.
        with self.assertLogs('traxdb.tasks', level='WARNING'):
            self._run()
