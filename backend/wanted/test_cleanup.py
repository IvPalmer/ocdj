"""Synthetic PostgreSQL integration proof; run in Actions, never local API/DB."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import threading
import uuid
from unittest.mock import patch

import requests
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from recognize.models import RecognizeJob
from wanted.models import MetadataCleanup, WantedItem
from wanted.serializers import WantedItemSerializer
from wanted.services.cleanup import capture_external_identification, capture_wanted, due_cleanups, process_cleanup, publish_recognition
from wanted.services.shazam import ingest
from wanted.test_cleanup_evidence import CODE, TITLE, evidence


def track(**changes):
    return {'artist': '', 'title': TITLE, 'timestamp_start': 0, 'timestamp_end': 30, **changes}


def raw(**changes):
    return [{'start_sec': 0, 'track': {'artist': '', 'title': TITLE, 'isrc': CODE, **changes}}]


class CleanupIntegrationTests(TestCase):
    def setUp(self):
        self.dispatch = patch('wanted.services.cleanup.dispatch').start()
        self.addCleanup(patch.stopall)
        self.collect = patch('wanted.services.cleanup.collect_evidence', return_value=evidence()).start()

    def item(self, **changes):
        return WantedItem.objects.create(artist='', title=TITLE, cleanup_isrc=CODE, **changes)

    def receipt(self, item):
        return MetadataCleanup.objects.get(subject_key=f'wanted:{item.pk}')

    def test_create_capture_snapshot_and_on_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            item = self.item()
        self.dispatch.assert_called_once_with(self.receipt(item).pk)
        self.assertEqual(self.receipt(item).snapshot['artist'], '')

    def test_create_and_receipt_roll_back_together(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.item()
            raise RuntimeError('synthetic failure')
        self.assertFalse(WantedItem.objects.exists())
        self.assertFalse(MetadataCleanup.objects.exists())
        self.dispatch.assert_not_called()

    def test_capture_failure_rolls_back_item(self):
        with patch('wanted.services.cleanup.capture_wanted', side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):
                self.item()
        self.assertFalse(WantedItem.objects.exists())

    def test_automatic_missing_artist_atomic_audit_and_idempotence(self):
        item = self.item()
        receipt = self.receipt(item)
        process_cleanup(receipt.pk)
        process_cleanup(receipt.pk)
        item.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(item.artist, 'Fixture Artist')
        self.assertEqual(item.title, TITLE)
        self.assertEqual(item.metadata_revision, 1)
        self.assertEqual(receipt.snapshot['artist'], '')
        self.assertEqual(receipt.result['changes'], {'artist': 'Fixture Artist'})
        self.assertEqual(receipt.status, 'applied')
        self.assertEqual(receipt.attempts, 1)
        self.assertEqual(receipt.evidence, evidence())
        self.collect.assert_called_once()

    def test_apply_and_receipt_update_roll_back_together(self):
        item = self.item()
        receipt = self.receipt(item)
        original_save = MetadataCleanup.save
        def fail_final(obj, *args, **kwargs):
            if obj.status == 'applied':
                raise RuntimeError('synthetic audit failure')
            return original_save(obj, *args, **kwargs)
        with patch.object(MetadataCleanup, 'save', fail_final):
            with self.assertRaises(RuntimeError):
                process_cleanup(receipt.pk)
        item.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(item.artist, '')
        self.assertEqual(item.metadata_revision, 0)
        self.assertEqual(receipt.status, 'researching')

    def test_same_blank_confirmation_fences_in_flight_result(self):
        item = self.item()
        def lookup(snapshot):
            serializer = WantedItemSerializer(item, data={'artist': ''}, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return evidence()
        self.collect.side_effect = lookup
        process_cleanup(self.receipt(item).pk)
        item.refresh_from_db()
        self.assertTrue(item.metadata_protected)
        self.assertEqual(item.artist, '')
        self.assertEqual(item.metadata_revision, 1)
        self.assertEqual(self.receipt(item).status, 'superseded')

    def test_edit_then_revert_is_protected(self):
        item = self.item()
        item.artist = 'Human correction'
        item.save(update_fields=['artist'])
        item.artist = ''
        item.save(update_fields=['artist'])
        process_cleanup(self.receipt(item).pk)
        item.refresh_from_db()
        self.assertEqual(item.artist, '')
        self.assertEqual(item.metadata_revision, 2)
        self.assertEqual(self.receipt(item).snapshot['revision'], 0)
        self.assertEqual(self.receipt(item).status, 'superseded')

    def test_stale_nonmetadata_save_preserves_correction(self):
        item = self.item()
        stale = WantedItem.objects.get(pk=item.pk)
        process_cleanup(self.receipt(item).pk)
        stale.status = 'found'
        stale.save()
        item.refresh_from_db()
        self.assertEqual(item.artist, 'Fixture Artist')
        self.assertEqual(item.status, 'found')
        self.assertFalse(item.metadata_protected)

    def test_partial_serializer_does_not_overwrite_other_corrected_fields(self):
        item = self.item()
        process_cleanup(self.receipt(item).pk)
        serializer = WantedItemSerializer(item, data={'title': 'Human title'}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        item.refresh_from_db()
        self.assertEqual(item.artist, 'Fixture Artist')
        self.assertEqual(item.title, 'Human title')
        self.assertTrue(item.metadata_protected)

    def test_delete_preserves_original_receipt_and_prevents_apply(self):
        item = self.item()
        receipt = self.receipt(item)
        item.delete()
        process_cleanup(receipt.pk)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'superseded')
        self.assertEqual(receipt.snapshot['title'], TITLE)

    def test_ambiguous_catalog_match_is_durable_proposal(self):
        item = self.item()
        ambiguous = evidence()
        ambiguous['candidates'].append(deepcopy(ambiguous['candidates'][0]))
        self.collect.return_value = ambiguous
        process_cleanup(self.receipt(item).pk)
        item.refresh_from_db()
        self.assertEqual(item.artist, '')
        self.assertEqual(self.receipt(item).status, 'proposed')
        self.assertEqual(len(self.receipt(item).evidence['candidates']), 2)

    def test_retry_budget_and_no_raw_error_text(self):
        item = self.item()
        receipt = self.receipt(item)
        self.collect.side_effect = requests.Timeout('DO_NOT_PUBLISH_PRIVATE_QUERY')
        for _ in range(3):
            MetadataCleanup.objects.filter(pk=receipt.pk).update(available_at=timezone.now())
            process_cleanup(receipt.pk)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'failed')
        self.assertEqual(receipt.attempts, 3)
        self.assertNotIn('DO_NOT_PUBLISH', str(receipt.result))
        process_cleanup(receipt.pk)
        self.assertEqual(self.collect.call_count, 3)

    def test_invalid_evidence_is_terminal(self):
        item = self.item()
        self.collect.side_effect = ValueError('invalid response')
        process_cleanup(self.receipt(item).pk)
        self.assertEqual(self.receipt(item).status, 'failed')

    def test_lost_dispatch_and_expired_claim_are_recoverable(self):
        item = self.item()
        receipt = self.receipt(item)
        self.assertIn(receipt, list(due_cleanups()))
        MetadataCleanup.objects.filter(pk=receipt.pk).update(status='researching', attempts=1,
                                                            available_at=timezone.now() - timedelta(seconds=1))
        process_cleanup(receipt.pk)
        self.assertEqual(self.receipt(item).status, 'applied')
        self.assertEqual(self.receipt(item).attempts, 2)

    def test_active_claim_and_terminal_exhausted_claim_do_not_lookup(self):
        item = self.item()
        receipt = self.receipt(item)
        MetadataCleanup.objects.filter(pk=receipt.pk).update(status='researching', attempts=1,
                                                            available_at=timezone.now() + timedelta(minutes=4))
        process_cleanup(receipt.pk)
        self.collect.assert_not_called()
        MetadataCleanup.objects.filter(pk=receipt.pk).update(attempts=3, available_at=timezone.now())
        process_cleanup(receipt.pk)
        self.collect.assert_not_called()
        self.assertEqual(self.receipt(item).status, 'failed')

    def test_old_worker_cannot_finalize_new_token(self):
        item = self.item()
        receipt = self.receipt(item)
        def lookup(snapshot):
            MetadataCleanup.objects.filter(pk=receipt.pk).update(lease=uuid.uuid4())
            return evidence()
        self.collect.side_effect = lookup
        process_cleanup(receipt.pk)
        item.refresh_from_db()
        self.assertEqual(item.artist, '')
        self.assertEqual(self.receipt(item).status, 'researching')

    def test_expired_worker_cannot_finalize(self):
        item = self.item()
        receipt = self.receipt(item)
        def lookup(snapshot):
            MetadataCleanup.objects.filter(pk=receipt.pk).update(available_at=timezone.now() - timedelta(seconds=1))
            return evidence()
        self.collect.side_effect = lookup
        process_cleanup(receipt.pk)
        item.refresh_from_db()
        self.assertEqual(item.artist, '')

    def test_shazam_bearer_and_spotify_shared_ingestion_captures_isrc(self):
        # ingest() is the actual common writer of bearer HTTP and Spotify poll.
        result = ingest([{'artist': '', 'title': TITLE, 'isrc': CODE}])
        self.assertEqual(result['created_count'], 1)
        item = WantedItem.objects.get()
        self.assertEqual(self.receipt(item).snapshot['isrc'], CODE)
        process_cleanup(self.receipt(item).pk)
        self.assertEqual(WantedItem.objects.get().artist, 'Fixture Artist')
        self.assertEqual(ingest([{'artist': 'Fixture Artist', 'title': TITLE}])['created_count'], 0)

    def test_external_hook_is_idempotent_and_proposal_only(self):
        event = uuid.uuid4()
        receipt = capture_external_identification(event_id=event, artist='', title=TITLE,
                                                   evidence={'source': 'caption'})
        replay = capture_external_identification(event_id=event, artist='', title=TITLE,
                                                  evidence={'source': 'caption'})
        self.assertEqual(receipt.pk, replay.pk)
        with self.assertRaises(ValueError):
            capture_external_identification(event_id=event, artist='Different', title=TITLE, evidence={})
        process_cleanup(receipt.pk)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'protected')
        self.assertEqual(receipt.result['changes'], {})

    def test_replay_after_edit_keeps_original_snapshot(self):
        item = self.item()
        before = self.receipt(item).snapshot
        item.title = 'Human title'
        item.save()
        self.assertEqual(capture_wanted(item).snapshot, before)
        self.assertEqual(MetadataCleanup.objects.count(), 1)

    def test_wanted_api_create_bulk_patch_and_readonly_audit(self):
        from wanted.views import WantedItemViewSet
        factory = APIRequestFactory()
        response = WantedItemViewSet.as_view({'post': 'create'})(factory.post('/', {'title': TITLE}, format='json'))
        self.assertEqual(response.status_code, 201)
        item_id = response.data['id']
        self.assertTrue(MetadataCleanup.objects.filter(subject_key=f'wanted:{item_id}').exists())
        response = WantedItemViewSet.as_view({'post': 'bulk_add'})(factory.post('/', {'items': [{'title': 'Fixture B'}]}, format='json'))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(MetadataCleanup.objects.count(), 2)
        response = WantedItemViewSet.as_view({'patch': 'partial_update'})(factory.patch('/', {'artist': ''}, format='json'), pk=item_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(WantedItem.objects.get(pk=item_id).metadata_protected)
        response = WantedItemViewSet.as_view({'get': 'cleanup'})(factory.get('/'), pk=item_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['snapshot']['title'], TITLE)

    def job(self, **changes):
        return RecognizeJob.objects.create(url='https://example.test/synthetic', status='completed',
                                            raw_results=raw(), **changes)

    def test_recognized_missing_artist_uses_shared_worker(self):
        job = self.job()
        publish_recognition(job, [track()])
        receipt = MetadataCleanup.objects.get(job_id=job.pk)
        self.assertEqual(receipt.snapshot['isrc'], CODE)
        process_cleanup(receipt.pk)
        job.refresh_from_db()
        self.assertEqual(job.tracklist[0]['artist'], 'Fixture Artist')
        self.assertEqual(job.tracklist[0]['title'], TITLE)
        self.assertEqual(receipt.snapshot['artist'], '')

    def test_actual_pipeline_completion_captures_and_retains_raw_results(self):
        from contextlib import ExitStack
        from recognize.services.pipeline import _recognize_worker
        job = self.job()
        # Fresh Shazam-only pass: all I/O and recognition are synthetic. This
        # exercises the actual final-save entry point, not just its helper.
        patches = {
            '_find_existing_audio': None,
            'lookup_by_url': None,
            'submit_url': None,
            'download_audio': ('/synthetic/audio.mp3', {'title': 'Synthetic mix', 'duration': 30}),
            'parse_tracklist_from_description': [],
            '_has_acrcloud_credentials': False,
            '_tuning': {'segment_duration': 10, 'segment_step': 20, 'gap_threshold': 30},
            'segment_audio': [('/synthetic/segment.mp3', 0)],
            'shazam_recognize_segments': raw(),
            'find_gaps': [],
            'find_single_segment_candidates': [],
            'cluster_results': [track()],
        }
        with ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(patch(f'recognize.services.pipeline.{name}', return_value=value))
            stack.enter_context(patch('recognize.services.pipeline.shutil.rmtree'))
            stack.enter_context(patch('recognize.services.pipeline.db.connections.close_all'))
            _recognize_worker(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.raw_results, raw())
        receipt = MetadataCleanup.objects.get(job_id=job.pk)
        self.assertEqual(receipt.snapshot['isrc'], CODE)

    def test_progress_save_does_not_erase_in_flight_manual_edit(self):
        from recognize.services.pipeline import _save_progress
        from recognize.views import update_track
        job = self.job(tracklist=[track()])
        response = update_track(APIRequestFactory().patch('/', {'artist': 'Human'}, format='json'), job.pk, 0)
        self.assertEqual(response.status_code, 200)
        job.segments_done = 5
        _save_progress(job)
        job.refresh_from_db()
        self.assertEqual(job.tracklist[0]['artist'], 'Human')
        self.assertTrue(job.tracklist[0]['manual'])

    def test_rerun_preserves_manual_rows(self):
        from recognize.views import rerun_job
        job = self.job(tracklist=[track(artist='Human', manual=True)])
        with patch('recognize.views.run_recognize'):
            response = rerun_job(APIRequestFactory().post('/'), job.pk)
        self.assertEqual(response.status_code, 202)
        job.refresh_from_db()
        self.assertEqual(job.tracklist[0]['artist'], 'Human')

    def test_publish_repeat_retains_applied_value_and_occurrence(self):
        job = self.job()
        publish_recognition(job, [track()])
        receipt = MetadataCleanup.objects.get(job_id=job.pk)
        process_cleanup(receipt.pk)
        publish_recognition(job, [track()])
        self.assertEqual(MetadataCleanup.objects.filter(job_id=job.pk).count(), 1)
        self.assertEqual(job.tracklist[0]['artist'], 'Fixture Artist')

    def test_wrong_recognized_artist_becomes_proposal(self):
        job = self.job()
        job.raw_results = raw(artist='Wrong tag')
        publish_recognition(job, [track(artist='Wrong tag')])
        receipt = MetadataCleanup.objects.get(job_id=job.pk)
        process_cleanup(receipt.pk)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'proposed')
        self.assertEqual(receipt.evidence['candidates'][0]['artist'], 'Fixture Artist')
        job.refresh_from_db()
        self.assertEqual(job.tracklist[0]['artist'], 'Wrong tag')

    def test_lossy_cluster_and_missing_raw_anchor_never_authorize(self):
        for hits in [[], raw(title='Night Signal (Original Mix)'), raw(isrc=''),
                     raw() + [{'start_sec': 10, 'track': {'artist': '', 'title': 'Night Signal (Club Mix)', 'isrc': CODE}}]]:
            job = self.job()
            job.raw_results = hits
            publish_recognition(job, [track()])
            self.assertEqual(MetadataCleanup.objects.get(job_id=job.pk).snapshot['isrc'], '')

    def test_manual_recognized_edit_during_lookup_wins(self):
        from recognize.views import update_track
        job = self.job()
        publish_recognition(job, [track()])
        receipt = MetadataCleanup.objects.get(job_id=job.pk)
        def lookup(snapshot):
            response = update_track(APIRequestFactory().patch('/', {'artist': 'Human'}, format='json'), job.pk, 0)
            self.assertEqual(response.status_code, 200)
            return evidence()
        self.collect.side_effect = lookup
        process_cleanup(receipt.pk)
        job.refresh_from_db()
        self.assertEqual(job.tracklist[0]['artist'], 'Human')
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'superseded')
        # Stale publisher must preserve the current manual row under its lock.
        publish_recognition(job, [track()])
        self.assertEqual(job.tracklist[0]['artist'], 'Human')
        self.assertTrue(job.tracklist[0]['manual'])

    def test_deleting_job_preserves_receipt_and_prevents_write(self):
        job = self.job()
        publish_recognition(job, [track()])
        receipt = MetadataCleanup.objects.get(job_id=job.pk)
        job.delete()
        process_cleanup(receipt.pk)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'superseded')

    def test_trackid_response_path_now_persists_occurrences(self):
        from recognize.views import trackid_lookup
        with patch('recognize.views.lookup_by_url', return_value={'tracklist': [track()], 'title': 'Fixture mix'}):
            response = trackid_lookup(APIRequestFactory().post('/', {'url': 'https://example.test/mix'}, format='json'))
        self.assertEqual(response.status_code, 200)
        job = RecognizeJob.objects.get(pk=response.data['job_id'])
        self.assertTrue(job.tracklist[0]['cleanup_id'])
        self.assertEqual(MetadataCleanup.objects.get(job_id=job.pk).snapshot['isrc'], '')

    def test_recluster_endpoint_captures_new_tracks_and_preserves_latest_manual(self):
        from recognize.views import recluster_job
        job = self.job()
        with patch('recognize.services.clustering.cluster_results', return_value=[track()]), \
             patch('recognize.views.lookup_by_url', return_value=None):
            response = recluster_job(APIRequestFactory().post('/'), job.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MetadataCleanup.objects.filter(job_id=job.pk).count(), 1)

    def test_recognize_to_wanted_keeps_manual_protection(self):
        from recognize.views import add_to_wanted
        job = self.job(tracklist=[track(artist='Human', manual=True)])
        response = add_to_wanted(APIRequestFactory().post('/', {'track_indices': [0]}, format='json'), job.pk)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(WantedItem.objects.get().metadata_protected)


class CleanupConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Real row-lock proof requires PostgreSQL in Actions')
        self.dispatch = patch('wanted.services.cleanup.dispatch').start()
        self.addCleanup(patch.stopall)

    def test_duplicate_workers_only_research_and_apply_once(self):
        item = WantedItem.objects.create(title=TITLE, cleanup_isrc=CODE)
        receipt = MetadataCleanup.objects.get(wanted_id=item.pk)
        entered, release = threading.Event(), threading.Event()
        def lookup(snapshot):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('synthetic timeout')
            return evidence()
        def worker():
            close_old_connections()
            try:
                process_cleanup(receipt.pk)
            finally:
                close_old_connections()
        with patch('wanted.services.cleanup.collect_evidence', side_effect=lookup) as collect:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(worker)
                self.assertTrue(entered.wait(10))
                second = pool.submit(worker)
                second.result(timeout=10)
                release.set()
                first.result(timeout=10)
        receipt.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(receipt.status, 'applied')
        self.assertEqual(item.metadata_revision, 1)

    def test_concurrent_manual_confirmation_fences_worker(self):
        item = WantedItem.objects.create(title=TITLE, cleanup_isrc=CODE)
        receipt = MetadataCleanup.objects.get(wanted_id=item.pk)
        entered, release = threading.Event(), threading.Event()
        def lookup(snapshot):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('synthetic timeout')
            return evidence()
        def worker():
            close_old_connections()
            try:
                process_cleanup(receipt.pk)
            finally:
                close_old_connections()
        with patch('wanted.services.cleanup.collect_evidence', side_effect=lookup):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(worker)
                self.assertTrue(entered.wait(10))
                serializer = WantedItemSerializer(item, data={'artist': ''}, partial=True)
                serializer.is_valid(raise_exception=True)
                serializer.save()
                release.set()
                future.result(timeout=10)
        item.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(item.artist, '')
        self.assertTrue(item.metadata_protected)
        self.assertEqual(receipt.status, 'superseded')
