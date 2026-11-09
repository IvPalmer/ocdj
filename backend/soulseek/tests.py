"""Synthetic clearing fixtures; never contact a real Soulseek peer."""
import copy
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

import requests
from django.test import TestCase, SimpleTestCase
from rest_framework.test import APIClient

from organize.models import PipelineItem
from .models import Download, SearchQueueItem, SearchResult
from .services import SlskdClient


class HistoryClientTests(SimpleTestCase):
    def test_history_removal_uses_delete_remove_and_encodes_peer(self):
        client = SlskdClient()
        client.session = Mock()
        client.session.delete.return_value.status_code = 204
        client.remove_download_history('peer /?#', 'transfer')
        client.session.put.assert_not_called()
        client.session.delete.assert_called_once_with(
            client._url('/transfers/downloads/peer%20%2F%3F%23/transfer'),
            params={'remove': 'true'}, timeout=30,
        )
        client.session.delete.return_value.raise_for_status.assert_called_once()

    def test_enqueue_and_cancel_encode_url_like_peer(self):
        client = SlskdClient()
        client.session = Mock()
        client.session.post.return_value.text = ''
        client.session.delete.return_value.status_code = 204
        peer = 'https://fixture.invalid'
        url = client._url('/transfers/downloads/https%3A%2F%2Ffixture.invalid')
        client.download(peer, 'song.flac')
        client.cancel_download(peer, 'id', remove=True)
        client.session.post.assert_called_once_with(url, json=[{'filename': 'song.flac'}], timeout=30)
        client.session.delete.assert_called_once_with(f'{url}/id', params={'remove': 'true'}, timeout=30)
        client.session.put.assert_not_called()

    def test_missing_history_is_idempotent_but_other_errors_propagate(self):
        client = SlskdClient()
        client.session = Mock()
        response = client.session.delete.return_value
        response.status_code = 404
        response.raise_for_status.side_effect = requests.HTTPError('fixture')
        client.remove_download_history('peer', 'gone')
        response.raise_for_status.assert_not_called()
        response.status_code = 500
        with self.assertRaises(requests.HTTPError):
            client.remove_download_history('peer', 'failed')


class ClearDownloadsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.transfers = []
        self.fail_ids = set()
        self.removed = []
        self.peer = SlskdClient()
        self.peer.session = Mock()
        self.peer.session.delete.side_effect = self.delete_history
        self.peer.get_downloads = lambda: [{'username': 'peer', 'directories': [
            {'files': copy.deepcopy(self.transfers)}]}]
        patcher = patch('soulseek.views.SlskdClient', return_value=self.peer)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.qi = SearchQueueItem.objects.create(raw_query='fixture', status='downloaded')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / 'audio.flac'
        self.audio.write_bytes(b'fixture audio must survive clearing')

    def delete_history(self, url, params=None, **kwargs):
        response = Mock(status_code=204)
        tid = url.rsplit('/', 1)[-1]
        if tid in self.fail_ids:
            raise requests.Timeout('synthetic timeout')
        self.assertEqual(params, {'remove': 'true'})
        self.assertTrue(any(t['id'] == tid and 'Completed' in t['state'] for t in self.transfers))
        self.removed.append(tid)
        self.transfers[:] = [t for t in self.transfers if t['id'] != tid]
        return response

    def add(self, status, state=None, tracked=True, tid=None):
        tid = tid or f't{len(self.transfers)}-{Download.objects.count()}'
        filename = f'fixture/{tid}.flac'
        dl = None
        if tracked:
            dl = Download.objects.create(username='peer', filename=filename,
                slskd_id=tid, status=status, queue_item=self.qi, local_path=str(self.audio))
        if state:
            self.transfers.append({'id': tid, 'filename': filename, 'state': state})
        return dl, tid

    def clear(self, mode):
        return self.client.post('/api/soulseek/downloads/clear/', {'mode': mode}, format='json')

    def test_history_does_not_hide_an_active_retry_of_same_file(self):
        dl, _ = self.add('failed', 'Completed, TimedOut')
        self.transfers.append({'id': 'retry', 'filename': dl.filename, 'state': 'Queued, Remotely'})
        self.assertEqual(self.client.delete(f'/api/soulseek/downloads/{dl.pk}/').status_code, 409)
        self.assertTrue(Download.objects.filter(pk=dl.pk).exists())
        self.peer.session.delete.assert_not_called()

    def test_cancel_resolves_missing_transfer_id_and_preserves_row_on_failure(self):
        dl, tid = self.add('queued', 'Queued, Remotely')
        dl.slskd_id = ''
        dl.save(update_fields=['slskd_id'])
        with patch.object(self.peer, 'cancel_download', side_effect=[requests.Timeout(), None]) as cancel:
            for code in (502, 200):
                result = self.client.post('/api/soulseek/downloads/cancel/', {'download_id': dl.pk}, format='json')
                self.assertEqual(result.status_code, code)
                dl.refresh_from_db()
                self.assertEqual(dl.status, 'queued' if code == 502 else 'cancelled')
                cancel.assert_called_with('peer', tid, remove=True)

    def test_both_groups_refetch_retry_preserve_active_audio_and_pipeline(self):
        complete, ct = self.add('completed', 'Completed, Succeeded')
        pipeline = PipelineItem.objects.create(download=complete,
            original_filename='audio.flac', current_path=str(self.audio))
        self.add('completed', 'Completed, Succeeded', tracked=False)
        expected_failed = []
        for state in ['Errored', 'TimedOut', 'Rejected', 'Cancelled']:
            expected_failed.append(self.add('cancelled' if state == 'Cancelled' else 'failed',
                f'Completed, {state}')[0].pk)
            self.add('failed', f'Completed, {state}', tracked=False)
        active = [self.add('queued', 'Queued, Remotely')[0],
                  self.add('downloading', 'InProgress')[0]]
        self.add('queued', 'Initializing', tracked=False)
        self.assertEqual(self.clear('completed').status_code, 200)
        self.assertFalse(Download.objects.filter(pk=complete.pk).exists())
        self.assertEqual(len(self.removed), 2)
        self.assertEqual(self.clear('completed').data['slskd_cleared'], 0)
        for _ in range(2):
            rows = self.client.get('/api/soulseek/downloads/').data['downloads']
            self.assertFalse(any(d['status'] == 'completed' for d in rows))
        self.assertEqual(self.clear('failed').status_code, 200)
        self.assertFalse(Download.objects.filter(pk__in=expected_failed).exists())
        self.assertEqual(len(self.removed), 10)
        self.assertEqual(self.clear('failed').data['slskd_cleared'], 0)
        for _ in range(2):
            rows = self.client.get('/api/soulseek/downloads/').data['downloads']
            self.assertTrue(all(d['status'] in ('queued', 'downloading') for d in rows))
        self.assertEqual(set(Download.objects.values_list('pk', flat=True)), {d.pk for d in active})
        self.assertEqual(len(self.transfers), 3)
        self.assertEqual(self.audio.read_bytes(), b'fixture audio must survive clearing')
        pipeline.refresh_from_db()
        self.assertIsNone(pipeline.download_id)
        self.assertEqual(pipeline.current_path, str(self.audio))
        self.qi.refresh_from_db()
        self.assertEqual(self.qi.status, 'downloaded')
        self.peer.session.put.assert_not_called()

    def test_partial_failure_retains_local_history_and_retry_finishes(self):
        good, good_id = self.add('failed', 'Completed, Errored')
        bad, bad_id = self.add('failed', 'Completed, TimedOut')
        self.fail_ids.add(bad_id)
        result = self.clear('failed')
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.data['slskd_cleared'], 1)
        self.assertEqual(result.data['slskd_failed'], 1)
        self.assertFalse(Download.objects.filter(pk=good.pk).exists())
        self.assertTrue(Download.objects.filter(pk=bad.pk).exists())
        self.assertTrue(any(d['id'] == bad.pk for d in self.client.get('/api/soulseek/downloads/').data['downloads']))
        self.fail_ids.clear()
        self.assertEqual(self.clear('failed').status_code, 200)
        self.assertFalse(Download.objects.exists())
        self.assertEqual(self.client.get('/api/soulseek/downloads/').data['downloads'], [])

    def test_unreachable_slskd_preserves_local_rows(self):
        dl, _ = self.add('completed')
        self.peer.get_downloads = Mock(side_effect=requests.Timeout('fixture'))
        self.assertEqual(self.clear('completed').status_code, 502)
        self.assertTrue(Download.objects.filter(pk=dl.pk).exists())

    def test_stale_terminal_local_status_must_not_remove_live_transfer(self):
        dl, _ = self.add('completed', 'InProgress')
        self.assertEqual(self.clear('all').status_code, 200)
        self.assertTrue(Download.objects.filter(pk=dl.pk).exists())
        self.assertEqual(self.removed, [])

    def test_remove_single_untracked_timeout_preserves_other_rows_and_audio(self):
        _, timed_out = self.add('failed', 'Completed, TimedOut', tracked=False)
        self.add('failed', 'Completed, TimedOut', tracked=False)
        self.add('queued', 'Queued, Remotely', tracked=False)
        payload = {'download_id': f'slskd:peer:{timed_out}'}
        for _ in range(2):
            self.assertEqual(self.client.delete('/api/soulseek/downloads/remove/', payload, format='json').status_code, 204)
        self.assertEqual(self.removed, [timed_out])
        self.assertEqual(len(self.transfers), 2)
        self.assertEqual(self.audio.read_bytes(), b'fixture audio must survive clearing')

    def test_remove_tracked_terminal_transfer_checks_live_state(self):
        dl, tid = self.add('failed', 'InProgress')
        url = f'/api/soulseek/downloads/{dl.pk}/'
        self.assertEqual(self.client.delete(url).status_code, 409)
        self.assertTrue(Download.objects.filter(pk=dl.pk).exists())
        self.transfers[0]['state'] = 'Completed, TimedOut'
        self.fail_ids.add(tid)
        self.assertEqual(self.client.delete(url).status_code, 502)
        self.assertTrue(Download.objects.filter(pk=dl.pk).exists())
        self.fail_ids.clear()
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.removed, [tid])
        self.assertFalse(Download.objects.filter(pk=dl.pk).exists())

    def test_local_only_rows_and_invalid_mode(self):
        self.add('completed')
        self.add('failed')
        self.add('cancelled')
        active, _ = self.add('queued')
        self.assertEqual(self.clear('invalid').status_code, 400)
        self.assertEqual(Download.objects.count(), 4)
        self.assertEqual(self.clear('all').status_code, 200)
        self.assertEqual(list(Download.objects.values_list('pk', flat=True)), [active.pk])

    def test_legacy_row_with_active_retry_is_preserved(self):
        dl, _ = self.add('completed', 'Completed, Succeeded')
        dl.slskd_id = ''
        dl.save()
        self.transfers.append({'id': 'retry', 'filename': dl.filename, 'state': 'InProgress'})
        self.assertEqual(self.clear('completed').status_code, 200)
        self.assertTrue(Download.objects.filter(pk=dl.pk).exists())
        self.assertEqual([t['id'] for t in self.transfers], ['retry'])

    def test_exact_id_does_not_clear_active_retry_of_same_filename(self):
        dl, _ = self.add('completed', 'Completed, Succeeded')
        self.transfers.append({'id': 'retry', 'filename': dl.filename, 'state': 'InProgress'})
        self.assertEqual(self.clear('completed').status_code, 200)
        self.assertFalse(Download.objects.filter(pk=dl.pk).exists())
        self.assertEqual([t['id'] for t in self.transfers], ['retry'])

    def test_non_string_mode_rejected_before_upstream_calls(self):
        for mode in [[], {}, None, 123]:
            self.assertEqual(self.clear(mode).status_code, 400)
        self.peer.session.delete.assert_not_called()


class SearchLockTests(TestCase):
    def test_search_keeps_lock_per_file_in_both_response_shapes(self):
        from .views import _run_search_for_queue_item
        item = SearchQueueItem.objects.create(raw_query='fixture', status='searching')
        peer = Mock()
        peer.search.return_value = {'id': 'fixture-search'}
        peer.get_search.return_value = {'state': 'Completed', 'responses': [{
            'username': 'fixture-peer',
            'files': [{'filename': 'open.flac'}, {'filename': 'flagged.flac', 'isLocked': True}],
            'lockedFiles': [{'filename': 'private.flac'}],
        }]}
        with patch('soulseek.views.SlskdClient', return_value=peer), \
             patch('soulseek.views.time.sleep'), patch('soulseek.views.db.connections.close_all'), \
             patch('soulseek.views.score_result', return_value=100):
            _run_search_for_queue_item(item.pk)
        self.assertEqual(dict(SearchResult.objects.filter(queue_item=item).values_list('filename', 'is_locked')),
                         {'open.flac': False, 'flagged.flac': True, 'private.flac': True})


class SearchCancellationTests(TestCase):
    def test_cancel_while_polling_deletes_peer_search_and_fences_late_results(self):
        from .views import _run_search_for_queue_item
        item = SearchQueueItem.objects.create(raw_query='fixture', status='searching')
        old = SearchResult.objects.create(queue_item=item, filename='previous.flac', username='peer')
        original_start = item.updated
        client = APIClient()
        peer = Mock()
        peer.search.return_value = {'id': 'fixture-search'}

        def cancel_then_retry(_seconds):
            result = client.post(f'/api/soulseek/queue/{item.pk}/cancel-search/', {}, format='json')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.data['status'], 'found')
            item.status = 'searching'
            item.save(update_fields=['status', 'updated'])

        with patch('soulseek.views.SlskdClient', return_value=peer), \
             patch('soulseek.views.time.sleep', side_effect=cancel_then_retry), \
             patch('soulseek.views.db.connections.close_all'):
            _run_search_for_queue_item(item.pk, original_start)
        item.refresh_from_db()
        self.assertEqual(item.status, 'searching')
        self.assertNotEqual(item.updated, original_start)
        self.assertEqual(list(item.search_results.values_list('pk', flat=True)), [old.pk])
        peer.delete_search.assert_called_once_with('fixture-search')
        peer.get_search.assert_not_called()

    def test_cancel_stale_search_restores_wanted_without_losing_metadata(self):
        from wanted.models import WantedItem
        wanted = WantedItem.objects.create(artist='Fixture artist', title='Fixture track', status='searching')
        item = SearchQueueItem.objects.create(wanted_item=wanted, status='searching')
        result = APIClient().post(f'/api/soulseek/queue/{item.pk}/cancel-search/', {}, format='json')
        self.assertEqual(result.status_code, 200)
        wanted.refresh_from_db()
        self.assertEqual(wanted.status, 'pending')
        self.assertEqual(wanted.artist, 'Fixture artist')
