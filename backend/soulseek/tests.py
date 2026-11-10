"""Synthetic clearing fixtures; never contact a real Soulseek peer."""
import copy
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

import requests
from django.test import TestCase, SimpleTestCase
from django.core.management import call_command
from django.utils import timezone
from io import StringIO
from wanted.models import WantedItem
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
        self.assertIsNotNone(Download.objects.get(pk=complete.pk).hidden_at)
        self.assertEqual(len(self.removed), 2)
        self.assertEqual(self.clear('completed').data['slskd_cleared'], 0)
        for _ in range(2):
            rows = self.client.get('/api/soulseek/downloads/').data['downloads']
            self.assertFalse(any(d['status'] == 'completed' for d in rows))
        self.assertEqual(self.clear('failed').status_code, 200)
        self.assertEqual(Download.objects.filter(pk__in=expected_failed, hidden_at__isnull=False).count(), 4)
        self.assertEqual(len(self.removed), 10)
        self.assertEqual(self.clear('failed').data['slskd_cleared'], 0)
        for _ in range(2):
            rows = self.client.get('/api/soulseek/downloads/').data['downloads']
            self.assertTrue(all(d['status'] in ('queued', 'downloading') for d in rows))
        self.assertEqual(set(Download.objects.filter(hidden_at__isnull=True).values_list('pk', flat=True)), {d.pk for d in active})
        self.assertEqual(len(self.transfers), 3)
        self.assertEqual(self.audio.read_bytes(), b'fixture audio must survive clearing')
        pipeline.refresh_from_db()
        self.assertEqual(pipeline.download_id, complete.pk)
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
        self.assertIsNotNone(Download.objects.get(pk=good.pk).hidden_at)
        self.assertTrue(Download.objects.filter(pk=bad.pk).exists())
        self.assertTrue(any(d['id'] == bad.pk for d in self.client.get('/api/soulseek/downloads/').data['downloads']))
        self.fail_ids.clear()
        self.assertEqual(self.clear('failed').status_code, 200)
        self.assertEqual(Download.objects.filter(hidden_at__isnull=False).count(), 2)
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
        self.assertIsNotNone(Download.objects.get(pk=dl.pk).hidden_at)

    def test_local_only_rows_and_invalid_mode(self):
        self.add('completed')
        self.add('failed')
        self.add('cancelled')
        active, _ = self.add('queued')
        self.assertEqual(self.clear('invalid').status_code, 400)
        self.assertEqual(Download.objects.count(), 4)
        self.assertEqual(self.clear('all').status_code, 200)
        self.assertEqual(list(Download.objects.filter(hidden_at__isnull=True).values_list('pk', flat=True)), [active.pk])

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
        self.assertIsNotNone(Download.objects.get(pk=dl.pk).hidden_at)
        self.assertEqual([t['id'] for t in self.transfers], ['retry'])

    def test_non_string_mode_rejected_before_upstream_calls(self):
        for mode in [[], {}, None, 123]:
            self.assertEqual(self.clear(mode).status_code, 400)
        self.peer.session.delete.assert_not_called()


    def link_wanted(self, status='downloading'):
        wanted = WantedItem.objects.create(artist='Fixture', title='Track', status=status)
        self.qi.wanted_item = wanted
        self.qi.status = status
        self.qi.save()
        return wanted

    def poll(self):
        return self.client.get('/api/soulseek/downloads/').data

    def assert_parent_status(self, wanted, expected):
        self.qi.refresh_from_db()
        wanted.refresh_from_db()
        self.assertEqual((self.qi.status, wanted.status), (expected, expected))

    def test_dismissal_retains_ids_relationships_and_is_idempotent(self):
        wanted = self.link_wanted()
        dl, tid = self.add('completed', 'Completed, Succeeded')
        sr = SearchResult.objects.create(queue_item=self.qi, wanted_item=wanted,
                                        username='peer', filename=dl.filename)
        dl.wanted_item = wanted
        dl.search_result = sr
        dl.save()
        pipeline = PipelineItem.objects.create(download=dl, wanted_item=wanted,
            original_filename='audio.flac', current_path=str(self.audio))
        url = f'/api/soulseek/downloads/{dl.pk}/'
        self.assertEqual(self.client.delete(url).status_code, 204)
        dl.refresh_from_db()
        stamp = dl.hidden_at
        self.assertIsNotNone(stamp)
        self.assertEqual(self.client.delete(url).status_code, 204)
        dl.refresh_from_db()
        pipeline.refresh_from_db()
        self.assertEqual(dl.hidden_at, stamp)
        self.assertEqual((dl.queue_item_id, dl.wanted_item_id, dl.search_result_id),
                         (self.qi.pk, wanted.pk, sr.pk))
        self.assertEqual(pipeline.download_id, dl.pk)
        self.assertEqual(self.removed, [tid])
        self.assertEqual(self.poll()['downloads'], [])
        # Indicators and reverse relations retain durable completion evidence.
        self.assertEqual(self.poll()['download_indicators'][dl.filename.lower()], 'completed')
        self.assertEqual(list(wanted.downloads.values_list('pk', flat=True)), [dl.pk])
        self.assert_parent_status(wanted, 'downloaded')

    def test_failed_clear_reconciles_all_known_attempts_including_queue_only_wanted_link(self):
        wanted = self.link_wanted()
        self.add('failed', 'Completed, Rejected')
        self.add('cancelled', 'Completed, Cancelled')
        self.assertEqual(self.clear('failed').data['cleared'], 2)
        self.assert_parent_status(wanted, 'found')
        self.assertEqual(self.clear('failed').data['cleared'], 0)

    def test_clear_and_cancel_do_not_reset_parent_with_other_active_attempt(self):
        wanted = self.link_wanted()
        self.add('failed', 'Completed, Errored')
        active, _ = self.add('queued')
        self.assertEqual(self.clear('failed').status_code, 200)
        self.assert_parent_status(wanted, 'downloading')
        another, _ = self.add('downloading')
        result = self.client.post('/api/soulseek/downloads/cancel/', {'download_id': active.pk}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assert_parent_status(wanted, 'downloading')
        self.assertEqual(self.client.post('/api/soulseek/downloads/cancel/',
            {'download_id': another.pk}, format='json').status_code, 200)
        self.assert_parent_status(wanted, 'found')

    def test_poll_does_not_confuse_same_file_attempts_or_collapse_untracked_retries(self):
        wanted = self.link_wanted()
        old, _ = self.add('queued', 'Completed, TimedOut')
        new = Download.objects.create(queue_item=self.qi, username='peer', filename=old.filename,
                                      status='queued', slskd_id='retry')
        self.transfers.append({'id': 'retry', 'filename': old.filename, 'state': 'InProgress'})
        self.transfers.append({'id': 'outside', 'filename': old.filename, 'state': 'Queued, Remotely'})
        payload = self.poll()
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual((old.status, new.status), ('failed', 'downloading'))
        self.assertEqual(payload['download_indicators'][old.filename.lower()], 'downloading')
        self.assertEqual([r['slskd_transfer_id'] for r in payload['downloads'] if r.get('untracked')], ['outside'])
        self.assert_parent_status(wanted, 'downloading')

    def test_hidden_terminal_cannot_absorb_or_reappear_as_a_retry(self):
        dl, tid = self.add('completed', 'Completed, Succeeded')
        self.assertEqual(self.clear('completed').status_code, 200)
        self.transfers.extend([
            {'id': tid, 'filename': dl.filename, 'state': 'Completed, Succeeded'},
            {'id': 'retry', 'filename': dl.filename, 'state': 'InProgress'},
            {'id': 'retry2', 'filename': dl.filename, 'state': 'Queued, Remotely'},
        ])
        for _ in range(2):
            payload = self.poll()
            self.assertEqual({r['slskd_transfer_id'] for r in payload['downloads']}, {'retry', 'retry2'})
            self.assertTrue(all(r.get('untracked') for r in payload['downloads']))
        dl.refresh_from_db()
        self.assertEqual(dl.status, 'completed')
        # Repeated clearing must not refresh an existing dismissal timestamp.
        stamp = dl.hidden_at
        self.assertEqual(self.clear('completed').data['cleared'], 0)
        dl.refresh_from_db()
        self.assertEqual(dl.hidden_at, stamp)

    def test_idless_retry_can_match_unique_new_transfer_beside_known_hidden_attempt(self):
        dl, tid = self.add('failed', 'Completed, TimedOut')
        self.assertEqual(self.clear('failed').status_code, 200)
        retry = Download.objects.create(username='peer', filename=dl.filename,
                                        queue_item=self.qi, status='queued')
        self.transfers.extend([
            {'id': tid, 'filename': dl.filename, 'state': 'Completed, TimedOut'},
            {'id': 'retry', 'filename': dl.filename, 'state': 'InProgress', 'percentComplete': 23},
        ])
        self.poll()
        retry.refresh_from_db()
        dl.refresh_from_db()
        self.assertEqual((retry.status, retry.progress), ('downloading', 23))
        self.assertEqual(dl.status, 'failed')

    def test_hidden_idless_local_history_cannot_claim_a_later_terminal_attempt(self):
        dl, _ = self.add('completed')
        dl.slskd_id = ''
        dl.save()
        self.assertEqual(self.clear('completed').status_code, 200)
        self.transfers.append({'id': 'later', 'filename': dl.filename, 'state': 'Completed, Succeeded'})
        rows = self.poll()['downloads']
        self.assertEqual([row['id'] for row in rows], ['slskd:peer:later'])

    def test_hidden_stale_identity_does_not_swallow_active_transfer(self):
        dl, tid = self.add('failed', 'Completed, Errored')
        self.assertEqual(self.clear('failed').status_code, 200)
        self.transfers.append({'id': tid, 'filename': dl.filename, 'state': 'InProgress'})
        rows = self.poll()['downloads']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].get('untracked'))
        self.assertEqual(rows[0]['status'], 'downloading')
        dl.refresh_from_db()
        self.assertEqual(dl.status, 'failed')

    def test_unknown_live_retry_prevents_failed_parent_reset(self):
        wanted = self.link_wanted()
        dl, _ = self.add('failed', 'Completed, TimedOut')
        self.transfers.append({'id': 'retry', 'filename': dl.filename, 'state': 'InProgress'})
        self.assertEqual(self.clear('failed').status_code, 200)
        self.assert_parent_status(wanted, 'downloading')
        # Once the retry is terminal the known failure can safely reconcile.
        self.transfers[-1]['state'] = 'Completed, Rejected'
        self.poll()
        self.assert_parent_status(wanted, 'found')

    def test_history_cap_never_hides_active_attempts_or_relabels_old_rows_untracked(self):
        active, _ = self.add('queued', 'Queued, Remotely')
        completed, _ = self.add('completed', 'Completed, Succeeded')
        for _ in range(51):
            self.add('failed')
        rows = self.poll()['downloads']
        self.assertIn(active.pk, {r['id'] for r in rows})
        self.assertNotIn(completed.pk, {r['id'] for r in rows})
        self.assertFalse(any(r.get('untracked') for r in rows))

    def test_stale_hidden_active_row_is_visible_and_can_complete_without_losing_identity(self):
        dl, _ = self.add('queued', 'InProgress')
        Download.objects.filter(pk=dl.pk).update(hidden_at=timezone.now())
        rows = self.poll()['downloads']
        self.assertEqual([(r['id'], r['status']) for r in rows], [(dl.pk, 'downloading')])
        self.transfers[0]['state'] = 'Completed, Succeeded'
        with self.captureOnCommitCallbacks(execute=False):
            self.assertEqual(self.poll()['downloads'], [])
        self.assertEqual(Download.objects.get(pk=dl.pk).status, 'completed')

    def test_poll_updates_parents_outside_history_limit_and_keeps_unknown_legacy(self):
        wanted = self.link_wanted()
        old, _ = self.add('queued', 'Completed, Rejected')
        for _ in range(51):
            Download.objects.create(username='other', filename='other.flac', status='failed')
        legacy = SearchQueueItem.objects.create(raw_query='unknown legacy', status='downloading')
        with self.captureOnCommitCallbacks(execute=False):
            self.poll()
        self.assert_parent_status(wanted, 'found')
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, 'downloading')
        old.refresh_from_db()
        self.assertEqual(old.status, 'failed')

    def test_single_actions_refuse_ambiguous_legacy_local_attempts(self):
        for local_status, state, method in [
            ('failed', 'Completed, Errored', 'dismiss'),
            ('queued', 'Queued, Remotely', 'cancel'),
        ]:
            with self.subTest(method=method):
                dl, _ = self.add(local_status, state)
                dl.slskd_id = ''
                dl.save()
                Download.objects.create(username='peer', filename=dl.filename, status=local_status)
                if method == 'dismiss':
                    result = self.client.delete(f'/api/soulseek/downloads/{dl.pk}/')
                else:
                    with patch.object(self.peer, 'cancel_download') as cancel:
                        result = self.client.post('/api/soulseek/downloads/cancel/',
                            {'download_id': dl.pk}, format='json')
                        cancel.assert_not_called()
                self.assertEqual(result.status_code, 409)
                dl.refresh_from_db()
                self.assertEqual(dl.status, local_status)
                self.assertIsNone(dl.hidden_at)
        self.assertEqual(self.removed, [])

    def test_ambiguous_legacy_attempts_are_not_assigned_one_transfer(self):
        old, _ = self.add('queued', 'Completed, Succeeded')
        old.slskd_id = ''
        old.save()
        other = Download.objects.create(username='peer', filename=old.filename, status='queued')
        payload = self.poll()
        old.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((old.status, other.status), ('queued', 'queued'))
        self.assertEqual(sum(bool(r.get('untracked')) for r in payload['downloads']), 1)

    def test_poll_unreachable_does_not_reset_failed_or_unknown_parents(self):
        wanted = self.link_wanted()
        self.add('failed')
        self.peer.get_downloads = Mock(side_effect=requests.Timeout('fixture'))
        self.assertTrue(self.poll()['slskd_unreachable'])
        self.assert_parent_status(wanted, 'downloading')

    def test_queue_only_enqueue_tracks_wanted_and_completion(self):
        wanted = self.link_wanted(status='found')
        with patch.object(self.peer, 'download', return_value={'enqueued': [{'id': 'new'}], 'failed': []}):
            result = self.client.post('/api/soulseek/download/', {
                'queue_item_id': self.qi.pk, 'username': 'peer', 'filename': 'new.flac',
            }, format='json')
        self.assertEqual(result.status_code, 201)
        self.assert_parent_status(wanted, 'downloading')
        self.transfers.append({'id': 'new', 'filename': 'new.flac', 'state': 'Completed, Succeeded'})
        with self.captureOnCommitCallbacks(execute=False):
            self.poll()
        self.assert_parent_status(wanted, 'downloaded')
        self.assertEqual(Download.objects.get(pk=result.data['id']).wanted_item_id, wanted.pk)

    def test_clear_captures_completion_before_removing_unpolled_transfer(self):
        wanted = self.link_wanted()
        dl, tid = self.add('queued', 'Completed, Succeeded')
        dl.wanted_item = wanted
        dl.save()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            result = self.clear('completed')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['cleared'], 1)
        dl.refresh_from_db()
        self.assertEqual((dl.status, dl.progress), ('completed', 100))
        self.assertIsNotNone(dl.completed_at)
        self.assertIsNotNone(dl.hidden_at)
        self.assertEqual(self.removed, [tid])
        self.assertEqual(len(callbacks), 1)
        self.assert_parent_status(wanted, 'downloaded')

    def test_hidden_completion_can_be_ingested_by_existing_pipeline_and_automation(self):
        from core.models import Config
        from core.services.automation import _step_auto_download, _step_auto_organize
        from organize.services.pipeline import scan_completed_downloads
        Config.objects.create(key='SOULSEEK_DOWNLOAD_ROOT', value=self.temp.name)
        wanted = self.link_wanted()
        dl, _ = self.add('completed', 'Completed, Succeeded')
        dl.wanted_item = wanted
        dl.save()
        SearchResult.objects.create(queue_item=self.qi, wanted_item=wanted,
            username='peer', filename=dl.filename, match_score=100)
        self.assertEqual(self.clear('completed').status_code, 200)
        # Even a stale `found` queue must not enqueue a duplicate hidden success.
        SearchQueueItem.objects.filter(pk=self.qi.pk).update(status='found')
        self.assertEqual(_step_auto_download(dry_run=True, threshold=90)['downloaded'], 0)
        self.assertEqual(_step_auto_organize(dry_run=True)['ingested'], 1)
        summary = scan_completed_downloads()
        self.assertEqual(summary['created_from_downloads'], 1)
        item = PipelineItem.objects.get(download_id=dl.pk)
        self.assertEqual(item.wanted_item_id, wanted.pk)
        self.assertEqual(Path(item.current_path).read_bytes(), b'fixture audio must survive clearing')
        self.assertEqual(scan_completed_downloads()['created'], 0)
        self.assertEqual(PipelineItem.objects.count(), 1)

    def test_poll_completion_and_parent_sync_are_idempotent(self):
        wanted = self.link_wanted()
        dl, _ = self.add('queued', 'Completed, Succeeded')
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.assertEqual(self.poll()['downloads'][0]['status'], 'completed')
        self.assertEqual(len(callbacks), 1)
        self.assert_parent_status(wanted, 'downloaded')
        dl.refresh_from_db()
        stamp = dl.completed_at
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.poll()
        self.assertEqual(callbacks, [])
        dl.refresh_from_db()
        self.assertEqual(dl.completed_at, stamp)

    def test_wanted_with_retry_on_other_queue_stays_downloading(self):
        wanted = self.link_wanted()
        self.add('failed')
        other = SearchQueueItem.objects.create(wanted_item=wanted, status='downloading')
        retry = Download.objects.create(username='other', filename='different.flac',
                                        queue_item=other, status='queued')
        self.poll()
        self.qi.refresh_from_db()
        wanted.refresh_from_db()
        self.assertEqual(self.qi.status, 'found')
        self.assertEqual(wanted.status, 'downloading')
        Download.objects.filter(pk=retry.pk).update(status='cancelled')
        self.poll()
        wanted.refresh_from_db()
        self.assertEqual(wanted.status, 'found')

    def test_unknown_other_queue_prevents_wanted_reset_and_later_states_are_preserved(self):
        wanted = self.link_wanted()
        self.add('failed')
        SearchQueueItem.objects.create(wanted_item=wanted, status='downloading')
        self.poll()
        wanted.refresh_from_db()
        self.assertEqual(wanted.status, 'downloading')
        WantedItem.objects.filter(pk=wanted.pk).update(status='organized')
        self.clear('failed')
        wanted.refresh_from_db()
        self.assertEqual(wanted.status, 'organized')

    def test_checker_does_not_complete_retry_from_older_same_file_attempt(self):
        wanted = self.link_wanted()
        old, _ = self.add('queued', 'Completed, Succeeded')
        retry = Download.objects.create(username='peer', filename=old.filename,
                                        queue_item=self.qi, status='queued', slskd_id='retry')
        self.transfers.append({'id': 'retry', 'filename': old.filename, 'state': 'Queued, Remotely'})
        self.peer.health = lambda: True
        with patch('soulseek.management.commands.check_downloads.SlskdClient', return_value=self.peer):
            call_command('check_downloads', stdout=StringIO())
        old.refresh_from_db()
        retry.refresh_from_db()
        self.assertEqual((old.status, retry.status), ('completed', 'queued'))
        self.assert_parent_status(wanted, 'downloading')

    def test_checker_uses_same_attempt_and_parent_semantics(self):
        wanted = self.link_wanted()
        dl, _ = self.add('queued', 'Completed, TimedOut')
        self.peer.health = lambda: True
        with patch('soulseek.management.commands.check_downloads.SlskdClient', return_value=self.peer):
            call_command('check_downloads', dry_run=True, stdout=StringIO())
            dl.refresh_from_db()
            self.assertEqual(dl.status, 'queued')
            self.assert_parent_status(wanted, 'downloading')
            call_command('check_downloads', stdout=StringIO())
        dl.refresh_from_db()
        self.assertEqual(dl.status, 'failed')
        self.assert_parent_status(wanted, 'found')


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
