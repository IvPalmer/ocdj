"""Mac local-download API + the sync's Mac-inventory awareness."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from traxdb.models import MacInventory, ScrapedFolder, ScrapedTrack

TOKEN = 'test-drain-token'
AUTH = {'HTTP_AUTHORIZATION': f'Bearer {TOKEN}'}


@override_settings(ALLOWED_HOSTS=['*'])
@patch.dict('os.environ', {'DRAIN_TOKEN': TOKEN})
class LocalDownloadAPITestCase(TestCase):
    def _folder(self, folder_id='abc', date='2026-07-15', status='pending', tracks=2):
        f = ScrapedFolder.objects.create(
            folder_id=folder_id,
            url='https://traxdb2.blogspot.com/p.html',
            pixeldrain_url=f'https://pixeldrain.com/l/{folder_id}',
            inferred_date=date,
            download_status=status,
        )
        for i in range(tracks):
            ScrapedTrack.objects.create(
                folder=f, filename=f'{folder_id}-{i}.flac',
                pixeldrain_file_id=f'{folder_id}file{i}', file_size_bytes=1000 + i,
            )
        return f

    def _claim(self, limit=1):
        resp = self.client.post(
            '/api/traxdb/local/claim/', data=json.dumps({'limit': limit}),
            content_type='application/json', **AUTH,
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()['items']

    def _post(self, path, body):
        return self.client.post(
            path, data=json.dumps(body), content_type='application/json', **AUTH,
        )

    def _all_files(self, folder):
        return [
            {'pixeldrain_file_id': t.pixeldrain_file_id, 'local_path': f'/Users/p/{t.filename}'}
            for t in folder.tracks.all()
        ]

    # ── auth ──────────────────────────────────────────────────

    def test_endpoints_reject_missing_token(self):
        resp = self.client.post('/api/traxdb/local/claim/', content_type='application/json')
        self.assertEqual(resp.status_code, 401)

    def test_endpoints_reject_wrong_token(self):
        resp = self.client.post(
            '/api/traxdb/local/claim/', content_type='application/json',
            **{'HTTP_AUTHORIZATION': 'Bearer nope'},
        )
        self.assertEqual(resp.status_code, 401)

    # ── claiming ──────────────────────────────────────────────

    def test_claim_leases_and_returns_track_details(self):
        f = self._folder()
        items = self._claim()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['folder_id'], 'abc')
        self.assertEqual(len(items[0]['tracks']), 2)
        self.assertTrue(items[0]['claim_token'])

        f.refresh_from_db()
        self.assertEqual(f.download_status, 'downloading')
        self.assertIsNotNone(f.claimed_at)

    def test_claimed_folder_is_not_handed_out_twice(self):
        self._folder()
        self._claim()
        self.assertEqual(self._claim(), [])

    def test_stale_claim_is_reoffered_with_a_new_token(self):
        f = self._folder()
        first = self._claim()[0]['claim_token']
        ScrapedFolder.objects.filter(pk=f.pk).update(
            claimed_at=timezone.now() - timedelta(days=1)
        )
        second = self._claim()
        self.assertEqual(len(second), 1)
        self.assertNotEqual(second[0]['claim_token'], first)

    def test_claim_skips_dates_the_mac_already_holds(self):
        """A folder queued before the Mac had that date must not be offered —
        downloading it would merge into a folder the operator pruned."""
        self._folder('held', '2026-07-15')
        MacInventory.report(['2026-07-15'])
        self.assertEqual(self._claim(), [])

    def test_claim_offers_only_one_folder_per_date(self):
        self._folder('one', '2026-07-15')
        self._folder('two', '2026-07-15')
        items = self._claim(limit=5)
        self.assertEqual(len(items), 1)

    def test_newest_lists_are_claimed_first(self):
        self._folder('old', '2026-06-01')
        self._folder('new', '2026-07-15')
        self.assertEqual(self._claim()[0]['folder_id'], 'new')

    # ── completion ────────────────────────────────────────────

    def test_complete_marks_tracks_and_folder(self):
        f = self._folder()
        token = self._claim()[0]['claim_token']
        resp = self._post(f'/api/traxdb/local/{f.pk}/complete/',
                          {'claim_token': token, 'files': self._all_files(f)})
        self.assertEqual(resp.status_code, 200)
        f.refresh_from_db()
        self.assertEqual(f.download_status, 'downloaded')
        self.assertEqual(f.claim_token, '')
        self.assertEqual(f.tracks.filter(downloaded=True).count(), 2)

    def test_complete_rejects_partial_file_set(self):
        """Half a folder marked 'downloaded' would hide the gap forever: the
        date enters the inventory and is never offered again."""
        f = self._folder()
        token = self._claim()[0]['claim_token']
        resp = self._post(f'/api/traxdb/local/{f.pk}/complete/', {
            'claim_token': token,
            'files': [{'pixeldrain_file_id': 'abcfile0', 'local_path': '/a'}],
        })
        self.assertEqual(resp.status_code, 400)
        f.refresh_from_db()
        self.assertEqual(f.download_status, 'downloading')

    def test_complete_rejects_stale_claim_token(self):
        f = self._folder()
        stale = self._claim()[0]['claim_token']
        ScrapedFolder.objects.filter(pk=f.pk).update(
            claimed_at=timezone.now() - timedelta(days=1)
        )
        self._claim()  # reassigns with a fresh token
        resp = self._post(f'/api/traxdb/local/{f.pk}/complete/',
                          {'claim_token': stale, 'files': self._all_files(f)})
        self.assertEqual(resp.status_code, 409)

    def test_complete_folds_date_into_mac_inventory(self):
        f = self._folder()
        token = self._claim()[0]['claim_token']
        self._post(f'/api/traxdb/local/{f.pk}/complete/',
                   {'claim_token': token, 'files': self._all_files(f)})
        self.assertIn('2026-07-15', MacInventory.current())

    def test_complete_is_idempotent_after_token_cleared(self):
        """The daemon replays a saved receipt when its first POST was lost."""
        f = self._folder()
        token = self._claim()[0]['claim_token']
        body = {'claim_token': token, 'files': self._all_files(f)}
        first = self._post(f'/api/traxdb/local/{f.pk}/complete/', body)
        second = self._post(f'/api/traxdb/local/{f.pk}/complete/', body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json().get('idempotent'))

    # ── failure ───────────────────────────────────────────────

    def test_fail_parks_folder(self):
        f = self._folder()
        token = self._claim()[0]['claim_token']
        resp = self._post(f'/api/traxdb/local/{f.pk}/fail/',
                          {'claim_token': token, 'reason': 'pixeldrain 404'})
        self.assertEqual(resp.status_code, 200)
        f.refresh_from_db()
        self.assertEqual(f.download_status, 'failed')

    def test_fail_cannot_undo_a_completed_download(self):
        """A worker whose lease expired must not clobber the winner's result."""
        f = self._folder()
        token = self._claim()[0]['claim_token']
        self._post(f'/api/traxdb/local/{f.pk}/complete/',
                   {'claim_token': token, 'files': self._all_files(f)})
        resp = self._post(f'/api/traxdb/local/{f.pk}/fail/',
                          {'claim_token': token, 'reason': 'late failure'})
        self.assertEqual(resp.status_code, 409)
        f.refresh_from_db()
        self.assertEqual(f.download_status, 'downloaded')

    # ── inventory ─────────────────────────────────────────────

    def test_inventory_report_replaces_listing(self):
        resp = self._post('/api/traxdb/local/inventory/',
                          {'date_dirs': ['2026-07-15', '2026-07-13', '2026-07-13']})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MacInventory.current(), ['2026-07-13', '2026-07-15'])

    def test_inventory_rejects_non_list(self):
        resp = self._post('/api/traxdb/local/inventory/', {'date_dirs': 'nope'})
        self.assertEqual(resp.status_code, 400)

    def test_merge_report_does_not_drop_existing_dates(self):
        MacInventory.report(['2026-07-13'])
        MacInventory.report(['2026-07-15'], merge=True)
        self.assertEqual(MacInventory.current(), ['2026-07-13', '2026-07-15'])


@override_settings(ALLOWED_HOSTS=['*'])
class VpsDownloadDisabledTestCase(TestCase):
    @patch('traxdb.views.get_config')
    def test_trigger_download_refuses_when_target_is_mac(self, get_config):
        get_config.return_value = 'mac'
        resp = self.client.post('/api/traxdb/download/', data={}, content_type='application/json')
        self.assertEqual(resp.status_code, 409)

    @patch('traxdb.views.get_config')
    def test_trigger_download_refuses_on_unknown_target(self, get_config):
        """Fail closed: only an explicit 'vps' may store audio on the server."""
        get_config.return_value = 'typo'
        resp = self.client.post('/api/traxdb/download/', data={}, content_type='application/json')
        self.assertEqual(resp.status_code, 409)

    @patch('traxdb.services.downloader.get_config')
    def test_run_download_service_refuses_in_mac_mode(self, get_config):
        """A task queued before the switch must not write audio to the VPS."""
        from django import db as django_db
        from traxdb.models import TraxDBOperation
        from traxdb.services.downloader import run_download
        get_config.side_effect = lambda k, *a, **kw: 'mac' if k == 'TRAXDB_DOWNLOAD_TARGET' else ''
        op = TraxDBOperation.objects.create(op_type='download', status='pending')
        # run_download's finally-block closes all connections, which would tear
        # down the TestCase transaction.
        with patch.object(django_db.connections, 'close_all'):
            run_download(op.id)
        op.refresh_from_db()
        self.assertEqual(op.status, 'failed')
        self.assertIn('belong on the Mac', op.error_message)


class SyncUsesMacInventoryTestCase(TestCase):
    """The pruned-folder guard has to read the Mac's folders, not the VPS's."""

    @patch('traxdb.services.scraper.get_config')
    def test_scan_uses_mac_inventory_and_derives_cutoff(self, get_config):
        get_config.side_effect = lambda k, *a, **kw: {
            'TRAXDB_DOWNLOAD_TARGET': 'mac',
        }.get(k, '')
        MacInventory.report(['2026-07-15', '2026-05-28', 'not-a-date'])

        from traxdb.services.scraper import _scan_local_inventory
        date_dirs, max_date, seen_ids, _ = _scan_local_inventory('/nonexistent')

        self.assertEqual(date_dirs, ['2026-05-28', '2026-07-15'])
        self.assertEqual(max_date, '2026-07-15')
        self.assertEqual(seen_ids, set())
