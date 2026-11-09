"""Synthetic accepted-event integration; PostgreSQL CI only, no providers."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from wanted.models import MetadataCleanup
from wanted.services.cleanup import capture_external_identification
from .models import Collection, Media, Post


class AcceptedCleanupTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.worker = APIClient()
        self.worker.credentials(HTTP_AUTHORIZATION='Bearer synthetic-token')
        env = patch.dict('os.environ', {'DRAIN_TOKEN': 'synthetic-token'})
        env.start()
        self.addCleanup(env.stop)
        collection = Collection.objects.create(instagram_id='1')
        self.post = Post.objects.create(collection=collection, instagram_id='2', rank=0,
                                       permalink='https://www.instagram.com/p/fixture/',
                                       caption='ID: Fixture Artist - Track (Dub Mix)')
        self.media = Media.objects.create(post=self.post, instagram_id='3', position=0, kind='video')
        self.candidate = {'artist': 'Fixture Artist', 'title': 'Track (Dub Mix)'}

    def caption(self):
        response = self.client.post(f'/api/instagram/media/{self.media.pk}/recognize/')
        self.assertEqual(response.status_code, 200)
        self.media.refresh_from_db()
        return response

    def lease(self, action='recognize'):
        self.media.queued = action == 'recognize'
        self.media.playback_requested = True
        self.media.lease_action = action
        self.media.lease_token = 'synthetic-lease'
        self.media.lease_until = timezone.now() + timedelta(minutes=5)
        self.media.save()

    def complete(self, **extra):
        data = dict(token='synthetic-lease', status='identified', candidates=[self.candidate])
        data.update(extra)
        return self.worker.post(f'/api/instagram/connector/media/{self.media.pk}/complete/', data, format='json')

    def receipt(self):
        self.media.refresh_from_db()
        event = self.media.recognition['cleanup_event']
        uuid.UUID(event)
        return MetadataCleanup.objects.get(subject_key=f'external:{event}')

    def test_caption_replay_is_one_protected_event_and_dispatch_after_commit(self):
        with patch('wanted.services.cleanup.dispatch') as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                self.caption()
                dispatch.assert_not_called()
            dispatch.assert_called_once()
        receipt = self.receipt()
        self.assertEqual(receipt.snapshot, dict(**self.candidate, isrc='', protected=True,
            source_evidence=dict(source='caption', route='caption', certainty='explicit')))
        self.caption()
        self.assertEqual(self.receipt().pk, receipt.pk)
        self.assertEqual(MetadataCleanup.objects.count(), 1)

    @patch('instagram.views.release_candidates')
    def test_catalog_capture_has_no_raw_record_or_anchor(self, lookup):
        self.post.caption = 'Catalog: FIX001'; self.post.save()
        lookup.return_value = ([self.candidate], [{'text': 'private caption', 'url': 'private', 'isrc': 'USAAA0000001'}])
        self.caption()
        self.assertEqual(self.receipt().snapshot['source_evidence'],
                         dict(source='caption', route='caption', certainty='explicit'))
        self.assertEqual(self.receipt().snapshot['isrc'], '')

    def test_connector_completion_and_duplicate_fence(self):
        self.lease()
        self.assertEqual(self.complete().status_code, 200)
        receipt = self.receipt()
        self.assertEqual(receipt.snapshot['source_evidence'],
                         dict(source='shazam', route='connector', certainty='tentative'))
        self.assertEqual(self.complete().status_code, 409)
        self.assertEqual(MetadataCleanup.objects.count(), 1)

    def test_later_accepted_identification_gets_new_event(self):
        self.lease(); self.complete()
        first = self.receipt()
        self.client.patch(f'/api/instagram/media/{self.media.pk}/', {'artist': '', 'title': ''}, format='json')
        self.media.refresh_from_db()
        self.lease(); self.complete()
        self.assertNotEqual(self.receipt().pk, first.pk)

    def test_reviewed_partial_and_conflicting_metadata_are_not_accepted(self):
        for artist, title, reviewed in [('Human', 'Edit', True), ('Human', '', False), ('Other', 'Track', False)]:
            with self.subTest(artist=artist, reviewed=reviewed):
                self.media.artist, self.media.title, self.media.reviewed = artist, title, reviewed
                self.media.save()
                self.caption()
                self.lease(); self.complete()
                self.media.refresh_from_db()
                self.assertEqual((self.media.artist, self.media.title, self.media.reviewed), (artist, title, reviewed))
        self.assertFalse(MetadataCleanup.objects.exists())

    def test_ambiguous_failed_stale_and_playback_results_do_not_capture(self):
        self.lease()
        self.assertEqual(self.complete(candidates=[self.candidate, dict(artist='Other', title='Mix')]).status_code, 200)
        self.lease()
        self.assertEqual(self.complete(status='no-match', candidates=[]).status_code, 200)
        self.lease()
        self.media.lease_until = timezone.now() - timedelta(seconds=1); self.media.save()
        self.assertEqual(self.complete().status_code, 409)
        self.lease('playback')
        with patch('wanted.services.cleanup.capture_external_identification') as capture:
            self.assertEqual(self.complete().status_code, 200)
            capture.assert_not_called()
        self.media.refresh_from_db()
        self.assertFalse(self.media.playback_requested)
        self.assertFalse(MetadataCleanup.objects.exists())

    def cached(self, result=None, effect=None):
        from .tasks import recognize_cached
        self.media.cache_key = 'synthetic'; self.media.cache_until = timezone.now() + timedelta(minutes=5)
        self.media.queued = True; self.media.save()
        # No file/audio/provider access; close the coroutine at the asyncio boundary.
        def run(coroutine):
            coroutine.close()
            if effect:
                effect()
            return result if result is not None else [self.candidate]
        with patch('shutil.copyfile'), patch('asyncio.run', side_effect=run):
            recognize_cached.call_local(self.media.pk)

    def test_cached_acceptance_and_task_redelivery(self):
        from .tasks import recognize_cached
        self.cached()
        self.assertEqual(self.receipt().snapshot['source_evidence'],
                         dict(source='shazam', route='cached', certainty='tentative'))
        recognize_cached.call_local(self.media.pk)
        self.assertEqual(MetadataCleanup.objects.count(), 1)

    def test_cached_manual_edit_fences_result_and_capture(self):
        def correct():
            Media.objects.filter(pk=self.media.pk).update(artist='Human', title='Edit', queued=False, lease_token='')
        self.cached(effect=correct)
        self.media.refresh_from_db()
        self.assertEqual(self.media.artist, 'Human')
        self.assertFalse(MetadataCleanup.objects.exists())

    def test_outer_rollback_removes_acceptance_receipt_and_dispatch(self):
        with patch('wanted.services.cleanup.dispatch') as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(RuntimeError), transaction.atomic():
                    self.caption()
                    self.assertEqual(MetadataCleanup.objects.count(), 1)
                    raise RuntimeError('synthetic outer rollback')
            dispatch.assert_not_called()
        self.media.refresh_from_db()
        self.assertEqual(self.media.artist, '')
        self.assertNotIn('cleanup_event', self.media.recognition)
        self.assertFalse(MetadataCleanup.objects.exists())

    def test_callback_database_failure_rolls_back_only_optional_capture(self):
        def fail(**kwargs):
            capture_external_identification(**kwargs)
            # A real PostgreSQL error must be contained by the adapter savepoint.
            MetadataCleanup.objects.create(subject_key=f"external:{kwargs['event_id']}", snapshot={})
        with patch('wanted.services.cleanup.capture_external_identification', side_effect=fail):
            self.caption()
        self.assertEqual(self.media.artist, self.candidate['artist'])
        self.assertFalse(MetadataCleanup.objects.exists())
        event = self.media.recognition['cleanup_event']
        self.caption()  # retry same accepted caption event, not a new UUID
        self.assertEqual(self.receipt().subject_key, f'external:{event}')

    def test_callback_failure_cannot_break_completion_or_cached_state(self):
        with patch('wanted.services.cleanup.capture_external_identification', side_effect=RuntimeError('private error')):
            self.lease(); self.assertEqual(self.complete().status_code, 200)
        self.media.refresh_from_db()
        self.assertFalse(self.media.queued)
        self.assertEqual(self.media.lease_token, '')
        self.assertEqual(self.media.artist, self.candidate['artist'])
        self.media.artist = ''; self.media.title = ''; self.media.save()
        with patch('wanted.services.cleanup.capture_external_identification', side_effect=IntegrityError('private error')):
            self.cached()
        self.media.refresh_from_db()
        self.assertFalse(self.media.queued)
        self.assertEqual(self.media.lease_token, '')
        self.assertEqual(self.media.status, 'identified')
        self.assertFalse(MetadataCleanup.objects.exists())
