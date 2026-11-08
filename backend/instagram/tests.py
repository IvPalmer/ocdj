import copy
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from wanted.models import WantedItem
from .models import Media, Post
from .serializers import COLLECTION_ID
from . import cache

FIXTURE = Path(__file__).resolve().parents[2] / 'tools/instagram/fixture.json'


@override_settings(INSTAGRAM_CACHE_ROOT=tempfile.mkdtemp(prefix='instagram-tests-'))
class WorkspaceTests(TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'DRAIN_TOKEN': 'test-token'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.client = APIClient()
        self.worker = APIClient(); self.worker.credentials(HTTP_AUTHORIZATION='Bearer test-token')
        self.data = json.loads(FIXTURE.read_text())
        self.sync()
        self.media = Media.objects.order_by('pk').first()

    def sync(self, data=None):
        r = self.worker.post('/api/instagram/connector/sync/', data or self.data, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        return r

    def action(self, name, pk=None):
        return self.client.post(f'/api/instagram/media/{pk or self.media.pk}/{name}/', {}, format='json')

    def claim(self):
        return self.worker.post('/api/instagram/connector/claim/', {}, format='json')

    def complete(self, job, status='identified', candidates=None):
        return self.worker.post(f"/api/instagram/connector/media/{job['id']}/complete/", {
            'token': job['token'], 'status': status,
            'candidates': candidates if candidates is not None else [{'artist': 'A', 'title': 'B'}]}, format='json')

    def upload(self, body=b'\x00\x00\x00\x18ftypisom' + b'x'*100, filename='../../escape.mp4', mime='video/mp4'):
        return self.worker.post(f'/api/instagram/connector/media/{self.media.pk}/upload/',
                                {'file': SimpleUploadedFile(filename, body, content_type=mime)})

    def test_preview_rejects_unrecognized_track(self):
        url = f'/api/instagram/media/{self.media.pk}/candidate-preview/'
        response = self.client.post(url, {'artist': 'Unknown', 'title': 'Track'}, format='json')
        self.assertEqual(response.status_code, 404)

    @patch('wanted.services.preview.requests.get')
    def test_preview_uses_exact_catalog_match_without_editing_candidate(self, get):
        candidate = {'artist': 'Luna', 'title': 'Track (Dub Mix)'}
        self.action('recognize')
        self.assertEqual(self.complete(self.claim().data, candidates=[candidate]).status_code, 200)
        url = f'/api/instagram/media/{self.media.pk}/candidate-preview/'
        preview_url = 'https://audio-ssl.itunes.apple.com/exact.m4a'
        track_url = 'https://music.apple.com/us/song/123'
        exact = {'artistName': 'LUNA', 'trackName': 'Track (Dub Mix)', 'previewUrl': preview_url,
                 'trackViewUrl': track_url}
        similar = [
            {'artistName': 'Another artist', 'trackName': candidate['title'], 'previewUrl': preview_url},
            {'artistName': 'Luna', 'trackName': 'Track (Radio Edit)', 'previewUrl': preview_url},
        ]
        get.return_value.json.return_value = {'results': [*similar, exact]}
        response = self.client.post(url, candidate, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'url': preview_url, 'provider': 'itunes', 'track_url': track_url})
        self.assertIn('no-store', response['Cache-Control'])
        get.assert_called_once()

        get.return_value.json.return_value = {'results': similar}
        self.assertEqual(self.client.post(url, candidate, format='json').data['url'], '')
        self.media.refresh_from_db()
        self.assertEqual(self.media.candidates, [candidate])
        self.assertFalse(self.media.reviewed)
        self.assertIsNone(self.media.wanted_id)

    def test_five_posts_all_eighteen_videos_in_saved_order(self):
        data = self.client.get('/api/instagram/').data
        self.assertEqual(len(data['posts']), 5)
        self.assertEqual(sum(m['kind'] == 'video' for p in data['posts'] for m in p['media']), 18)
        self.assertEqual([p['author'] for p in data['posts']], [p['author'] for p in self.data['posts']])
        self.assertEqual(len(data['posts'][0]['media']), 6)
        # Newest saved order is the supplied order, never the post ID/publication date.
        reversed_data = copy.deepcopy(self.data); reversed_data['posts'].reverse()
        self.sync(reversed_data)
        self.assertEqual(self.client.get('/api/instagram/').data['posts'][0]['author'], 'fixture_selector_5')

    def test_resync_preserves_review_correction_wishlist_and_children_reorder(self):
        url = f'/api/instagram/media/{self.media.pk}/'
        self.assertEqual(self.client.patch(url, {'artist': 'Artist', 'title': 'Title', 'reviewed': True}, format='json').status_code, 200)
        first = self.action('wishlist').data['wanted_id']
        self.assertEqual(first, self.action('wishlist').data['wanted_id'])
        data = copy.deepcopy(self.data); data['posts'][0]['children'].reverse()
        self.sync(data)
        self.media.refresh_from_db()
        self.assertEqual((self.media.artist, self.media.title, self.media.reviewed, self.media.wanted_id), ('Artist', 'Title', True, first))
        self.assertEqual(self.media.position, 5)

    def test_wishlist_correction_relinks_current_match_without_mutating_old_item(self):
        url = f'/api/instagram/media/{self.media.pk}/'
        self.client.patch(url, {'artist': 'Artist A', 'title': 'Track A', 'reviewed': True}, format='json')
        original = self.action('wishlist').data
        self.assertTrue(original['wanted_matches_current'])
        corrected = self.client.patch(url, {'artist': 'Artist B', 'title': 'Track B'}, format='json').data
        self.assertFalse(corrected['wanted_matches_current'])
        self.assertTrue(corrected['reviewed'])
        current = self.action('wishlist').data
        self.assertTrue(current['wanted_matches_current'])
        self.assertNotEqual(current['wanted_id'], original['wanted_id'])
        self.assertEqual(self.action('wishlist').data['wanted_id'], current['wanted_id'])
        self.assertEqual(WantedItem.objects.filter(artist='Artist B', title='Track B').count(), 1)
        old = WantedItem.objects.get(pk=original['wanted_id'])
        self.assertEqual((old.artist, old.title), ('Artist A', 'Track A'))
        self.media.refresh_from_db()
        self.assertTrue(self.media.reviewed)
        # Reverting the correction can reuse A, and case-only edits still match.
        self.client.patch(url, {'artist': 'artist a', 'title': 'track a'}, format='json')
        self.assertEqual(self.action('wishlist').data['wanted_id'], old.pk)
        self.assertTrue(self.client.get('/api/instagram/').data['posts'][0]['media'][0]['wanted_matches_current'])

    def test_later_video_recognition_dedupe_claim_and_stale_fence(self):
        second = Media.objects.get(post=self.media.post, position=1)
        self.action('recognize', second.pk); self.action('recognize', second.pk)
        job = self.claim().data
        self.assertEqual(job['id'], second.pk)
        self.assertEqual(self.claim().status_code, 204)
        second.refresh_from_db(); second.lease_until = timezone.now()-timedelta(seconds=1); second.save()
        replacement = self.claim().data
        self.assertNotEqual(job['token'], replacement['token'])
        self.assertEqual(self.complete(job).status_code, 409)
        self.assertEqual(self.complete(replacement).status_code, 200)
        second.refresh_from_db()
        self.assertEqual(second.status, 'identified'); self.assertFalse(second.reviewed)
        self.assertEqual(self.complete(replacement).status_code, 409)

    def test_correction_fences_inflight_result(self):
        self.action('recognize'); job = self.claim().data
        self.client.patch(f'/api/instagram/media/{self.media.pk}/', {'artist': 'Human', 'title': 'Correction'}, format='json')
        self.assertEqual(self.complete(job).status_code, 409)
        self.media.refresh_from_db(); self.assertEqual(self.media.artist, 'Human')

    def test_no_match_and_error_are_distinct_and_preserve_review(self):
        for status in ['no-match', 'error']:
            self.action('recognize'); job = self.claim().data
            self.assertEqual(self.complete(job, status, []).status_code, 200)
            self.media.refresh_from_db(); self.assertEqual(self.media.status, status)
        self.action('recognize'); job = self.claim().data
        self.assertEqual(self.complete(job, 'identified', []).status_code, 400)

    def test_auth_disabled_bad_and_valid(self):
        routes = ['heartbeat/', 'sync/', 'claim/',
                  f'media/{self.media.pk}/upload/', f'media/{self.media.pk}/complete/']
        for route in routes:
            url = '/api/instagram/connector/'+route
            with self.subTest(route=route):
                self.client.credentials()
                self.assertEqual(self.client.post(url, {}).status_code, 401)
                self.client.credentials(HTTP_AUTHORIZATION='Bearer wrong')
                self.assertEqual(self.client.post(url, {}).status_code, 401)
                with patch.dict(os.environ, {'DRAIN_TOKEN': ''}):
                    self.assertEqual(self.worker.post(url, {}).status_code, 503)

    def test_strict_sync_source_and_count_validation(self):
        for mutate in [lambda d: d.update(collection_id='other'),
                       lambda d: d['posts'].append(d['posts'][0]),
                       lambda d: d['posts'][0].update(permalink='http://127.0.0.1/private'),
                       lambda d: d['posts'][0]['children'][0].update(id='../../escape'),
                       lambda d: d['posts'][0]['children'][0].update(kind='script')]:
            d = copy.deepcopy(self.data); mutate(d)
            self.assertEqual(self.worker.post('/api/instagram/connector/sync/', d, format='json').status_code, 400)
        d = copy.deepcopy(self.data)
        d['posts'][1]['children'][0]['id'] = d['posts'][0]['children'][0]['id']
        self.assertEqual(self.worker.post('/api/instagram/connector/sync/', d, format='json').status_code, 400)

    def test_cache_ranges_expiry_and_no_raw_path(self):
        self.assertEqual(self.upload().status_code, 200)
        self.media.refresh_from_db()
        data = self.client.get('/api/instagram/').data['posts'][0]['media'][0]
        self.assertNotIn('cache_key', data)
        url = data['playback_url']
        r = self.client.get(url, HTTP_RANGE='bytes=4-7')
        self.assertEqual(r.status_code, 206); self.assertEqual(b''.join(r.streaming_content), b'ftyp')
        self.assertEqual(r['Content-Range'], 'bytes 4-7/112')
        r = self.client.get(url, HTTP_RANGE='bytes=-3')
        self.assertEqual(b''.join(r.streaming_content), b'xxx')
        self.assertEqual(self.client.head(url)['Content-Length'], '112')
        for value in ['bytes=999-', 'bytes=3-1', 'bytes=0-1,4-7', 'bytes=-']:
            self.assertEqual(self.client.get(url, HTTP_RANGE=value).status_code, 416)
        self.media.cache_until = timezone.now()-timedelta(seconds=1); self.media.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        owned = cache.path(self.media.cache_key)
        os.utime(owned, (timezone.now().timestamp()-301,)*2)
        unrelated = cache.root() / 'music-library.flac'; unrelated.write_bytes(b'untouched')
        cache.cleanup(); self.media.refresh_from_db(); self.assertEqual(self.media.cache_key, '')
        self.assertFalse(owned.exists()); self.assertEqual(unrelated.read_bytes(), b'untouched')

    def test_bad_cache_upload_and_path_rejected(self):
        self.assertEqual(self.upload(mime='text/html').status_code, 400)
        self.assertEqual(self.upload(body=b'<script>notmp4</script>').status_code, 400)
        with patch.object(cache, 'MAX_BYTES', 20):
            self.assertEqual(self.upload().status_code, 400)
        with patch.object(cache, 'MAX_TOTAL_BYTES', 10):
            self.assertEqual(self.upload().status_code, 400)
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError): cache.path('../../escape')
        self.assertFalse(Media.objects.get(pk=self.media.pk).cache_key)

    def test_inactive_cache_and_jobs_unavailable_after_sync(self):
        self.upload(); self.action('recognize')
        d = copy.deepcopy(self.data); d['posts'] = d['posts'][1:]
        self.sync(d)
        self.assertEqual(self.claim().status_code, 204)
        self.media.refresh_from_db(); self.assertEqual(self.media.cache_key, '')
        self.assertEqual(self.action('wishlist').status_code, 404)

    def test_images_cannot_queue(self):
        image = Media.objects.get(kind='image')
        self.assertEqual(self.action('recognize', image.pk).status_code, 400)

    def test_complete_collection_uses_native_models_and_keeps_order(self):
        data = copy.deepcopy(self.data)
        for i in range(251):
            data['posts'].append(dict(id=str(900000+i), permalink=f'https://www.instagram.com/p/fixture{i}/',
                author='fixture', caption='', children=[dict(id=str(800000+i), kind='video')]))
        self.sync(data)
        workspace = self.client.get('/api/instagram/').data
        self.assertEqual(len(workspace['posts']), 256)
        self.assertEqual(workspace['posts'][-1]['permalink'], data['posts'][-1]['permalink'])
        self.media.refresh_from_db()
        self.assertEqual(self.media.pk, workspace['posts'][0]['media'][0]['id'])

    def test_caption_id_recognizes_without_mac_or_playback(self):
        self.media.post.caption = '1. Fixture Artist - Track (Dub Mix)\n2. Other - Another'
        self.media.post.save()
        response = self.action('recognize')
        self.assertEqual(response.data['status'], 'identified')
        self.assertEqual(response.data['title'], 'Track (Dub Mix)')
        self.assertEqual(response.data['recognition']['source'], 'caption')
        self.assertEqual(response.data['recognition']['certainty'], 'explicit')
        self.assertFalse(response.data['reviewed'])
        self.assertEqual(self.claim().status_code, 204)

    def test_selected_playback_claim_does_not_overwrite_recognition(self):
        self.media.artist = 'Human'; self.media.title = 'Correction'; self.media.reviewed = True
        self.media.status = 'identified'; self.media.save()
        self.action('playback')
        job = self.claim().data
        self.assertEqual(job['action'], 'playback')
        self.assertEqual(job['post_instagram_id'], self.media.post.instagram_id)
        self.assertEqual(self.complete(job, 'no-match', []).status_code, 200)
        self.media.refresh_from_db()
        self.assertFalse(self.media.playback_requested)
        self.assertEqual((self.media.artist, self.media.title, self.media.status), ('Human', 'Correction', 'identified'))

    def test_recognize_during_playback_lease_stays_queued(self):
        self.action('playback'); job = self.claim().data
        self.action('recognize')
        self.complete(job, 'no-match', [])
        self.media.refresh_from_db()
        self.assertTrue(self.media.queued)
        self.assertEqual(self.media.status, 'pending')
        self.assertEqual(self.claim().data['action'], 'recognize')

    def test_cached_recognition_runs_on_server_and_fences_manual_edits(self):
        from .tasks import recognize_cached
        async def match(*args): return [{'artist': 'Fixture', 'title': 'Source audio'}]
        self.upload()
        with patch('instagram.tasks.recognize_cached') as enqueue:
            with self.captureOnCommitCallbacks(execute=True): self.action('recognize')
            enqueue.assert_called_once_with(self.media.pk)
        with patch('instagram.recognition.recognize', side_effect=match):
            recognize_cached.call_local(self.media.pk)
        self.media.refresh_from_db()
        self.assertEqual(self.media.status, 'identified')
        self.assertEqual(self.media.recognition['source'], 'shazam')
        self.assertFalse(self.media.queued)
        self.action('recognize')
        # A sync callback avoids Django ORM inside asyncio in the real task test.
        def concurrent(*args):
            Media.objects.filter(pk=self.media.pk).update(lease_token='', queued=False, artist='Human', title='Edited')
            return match()
        with patch('instagram.recognition.recognize', side_effect=concurrent):
            recognize_cached.call_local(self.media.pk)
        self.media.refresh_from_db()
        self.assertEqual(self.media.artist, 'Human')

    def test_cached_failure_is_error_not_no_match(self):
        from .tasks import recognize_cached
        self.upload(); self.action('recognize')
        with patch('instagram.recognition.recognize', side_effect=RuntimeError('fixture error')):
            recognize_cached.call_local(self.media.pk)
        self.media.refresh_from_db()
        self.assertEqual(self.media.status, 'error')
        self.assertEqual(self.media.recognition['outcome'], 'error')

    def test_images_load_in_native_private_view_and_cannot_recognize(self):
        image = Media.objects.get(kind='image')
        self.assertEqual(self.action('playback', image.pk).status_code, 200)
        job = self.claim().data
        self.assertEqual(job['id'], image.pk)
        response = self.worker.post(f'/api/instagram/connector/media/{image.pk}/upload/',
            {'file': SimpleUploadedFile('source.jpg', b'\xff\xd8\xff' + b'x'*100, content_type='image/jpeg')})
        self.assertEqual(response.status_code, 200)
        self.complete(job, 'no-match', [])
        response = self.client.get(f'/api/instagram/media/{image.pk}/stream/')
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(b''.join(response.streaming_content)[:3], b'\xff\xd8\xff')
