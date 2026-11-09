"""CI PostgreSQL only: opaque request -> lease -> staging -> browser Range."""
from datetime import timedelta
from io import BytesIO
import os
import tempfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import LibraryTrack, MacPlayback
from . import playback_cache as cache


class MacPlaybackTests(TestCase):
    # Twelve synthetic MPEG-1 Layer III frames: sufficient for server parsing
    # and byte/Range contracts. Real codec decoding is covered by helper/browser
    # fixtures, without making every consumer of library tests install FFmpeg.
    mp3 = (b'\xff\xfb\x90\x00' + b'\0' * 413) * 12
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.settings = override_settings(MAC_PLAYBACK_CACHE_ROOT=self.temp.name)
        self.settings.enable(); self.addCleanup(self.settings.disable)
        self.env = patch.dict(os.environ, {'DRAIN_TOKEN': 'fixture-token'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.client = APIClient()
        self.collection = '12345678-1234-5678-9abc-123456789abc'
        self.track = LibraryTrack.objects.create(source='mac_snapshot',
            file_path=f'mac-snapshot:{self.collection}:' + 'a' * 64, format='wav')
        self.url = f'/api/library/tracks/{self.track.pk}/playback/'
        self.audio_url = f'/api/library/tracks/{self.track.pk}/audio/'
        self.claim_url = '/api/library/playback/claim/'
        self.complete_url = f'/api/library/playback/{self.track.pk}/complete/'
        self.auth = {'HTTP_AUTHORIZATION': 'Bearer fixture-token'}

    def claim(self):
        return self.client.post(self.claim_url, {'collection_id': self.collection}, **self.auth)

    def stage(self):
        self.client.post(self.url, {})
        lease = self.claim().data['token']
        return self.client.post(self.complete_url, self.mp3, content_type='audio/mpeg',
            HTTP_X_PLAYBACK_LEASE=lease, **self.auth)

    def test_normal_entry_and_range_and_head(self):
        # Library rows have a precise record ID even without tags or with duplicate
        # artist/title matches. They need no guess through the match endpoint.
        detail = self.client.get(f'/api/library/tracks/{self.track.pk}/').data
        self.assertEqual(detail['playback_id'], self.track.pk)
        self.assertEqual(detail['file_path'], '')
        self.assertEqual(detail['filename'], '')
        self.assertEqual(self.client.get(self.url).data, {'status': 'offline'})
        self.assertEqual(self.stage().status_code, 200)
        ready = self.client.get(self.url)
        self.assertEqual(ready.data['status'], 'ready')
        self.assertEqual(ready.data['url'], self.audio_url)
        self.assertEqual(ready['Cache-Control'], 'private, no-store')
        self.assertEqual(self.claim().status_code, 204)
        response = self.client.get(self.audio_url, HTTP_RANGE='bytes=4-7')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), self.mp3[4:8])
        self.assertEqual(response['Content-Range'], f'bytes 4-7/{len(self.mp3)}')
        response = self.client.get(self.audio_url, HTTP_RANGE='bytes=-3')
        self.assertEqual(b''.join(response.streaming_content), self.mp3[-3:])
        response = self.client.head(self.audio_url)
        self.assertEqual(int(response['Content-Length']), len(self.mp3))
        self.assertEqual(response['Content-Type'], 'audio/mpeg')
        self.assertEqual(response.content, b'')
        for value in ['bytes=-0', 'bytes=2-1', 'bytes=1-2,4-5', 'bytes=-', 'bytes=' + '9' * 500 + '-', f'bytes={len(self.mp3)}-']:
            self.assertEqual(self.client.get(self.audio_url, HTTP_RANGE=value).status_code, 416)

    def test_no_path_or_bearer_bypass(self):
        for url in [self.claim_url, self.complete_url]:
            self.assertEqual(self.client.post(url, {}).status_code, 401)
            with patch.dict(os.environ, {'DRAIN_TOKEN': ''}):
                self.assertEqual(self.client.post(url, {}).status_code, 503)
        self.client.post(self.url, {})
        data = self.claim().data
        self.assertEqual(set(data), {'track_id', 'collection_id', 'opaque_id', 'token'})
        self.assertNotIn('file_path', str(data))
        self.assertEqual(self.claim().status_code, 204)
        self.assertEqual(self.client.post(self.complete_url, self.mp3, content_type='audio/mpeg',
            HTTP_X_PLAYBACK_LEASE='wrong', **self.auth).status_code, 409)

    def test_expiry_missing_retry_and_stale_completion(self):
        self.client.post(self.url, {})
        lease = self.claim().data['token']
        MacPlayback.objects.update(requested_until=timezone.now()-timedelta(seconds=1), lease_until=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.get(self.url).data['status'], 'offline')
        self.assertEqual(self.client.post(self.complete_url, self.mp3, content_type='audio/mpeg',
            HTTP_X_PLAYBACK_LEASE=lease, **self.auth).status_code, 409)
        self.assertEqual(self.stage().status_code, 200)
        MacPlayback.objects.update(cache_until=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.get(self.audio_url).status_code, 410)
        self.assertEqual(self.client.post(self.url, {}).data['status'], 'pending')
        self.assertEqual(self.claim().status_code, 200)
        self.track.missing = True; self.track.save()
        self.assertEqual(self.client.get(self.audio_url).status_code, 410)
        self.assertEqual(self.client.post(self.url, {}).status_code, 409)

    def test_failure_and_wrong_collection_are_explicit(self):
        self.client.post(self.url, {})
        self.assertEqual(self.client.post(self.claim_url, {'collection_id': 'bad'}, **self.auth).status_code, 400)
        self.assertEqual(self.client.post(self.claim_url, {'collection_id': '00000000-0000-0000-0000-000000000000'}, **self.auth).status_code, 204)
        token = self.claim().data['token']
        self.assertEqual(self.client.post(self.complete_url, {}, format='json', HTTP_X_PLAYBACK_LEASE=token, **self.auth).status_code, 200)
        self.assertEqual(self.client.get(self.url).data['status'], 'unavailable')
        self.assertEqual(self.client.post(self.url, {}).data['status'], 'pending')

    def test_cache_quota_invalid_media_and_stale_upload_cleanup(self):
        with patch.object(cache, 'MAX_BYTES', 2):
            with self.assertRaises(ValueError), cache.staged(BytesIO(self.mp3)):
                pass
        with patch.object(cache, 'MAX_TOTAL', 2):
            with self.assertRaises(ValueError), cache.staged(BytesIO(self.mp3)):
                pass
        with self.assertRaises(Exception), cache.staged(BytesIO(b'x' * 100)):
            pass
        self.assertFalse(any(cache.KEY.fullmatch(p.name) for p in cache.root().iterdir()))
        self.client.post(self.url, {})
        token = self.claim().data['token']
        original = cache.MP3
        def expire(path):
            MacPlayback.objects.update(lease_until=timezone.now()-timedelta(seconds=1))
            return original(path)
        with patch.object(cache, 'MP3', side_effect=expire):
            response = self.client.post(self.complete_url, self.mp3, content_type='audio/mpeg',
                HTTP_X_PLAYBACK_LEASE=token, **self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(any(cache.KEY.fullmatch(p.name) for p in cache.root().iterdir()))

    def test_cache_cleanup_and_missing_cache_reclaim(self):
        self.assertEqual(self.stage().status_code, 200)
        job = MacPlayback.objects.get()
        path = cache.path(job.cache_key)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        os.utime(path, (0, 0)); cache.cleanup()
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(self.audio_url).status_code, 410)
        self.assertEqual(self.client.post(self.url, {}).data['status'], 'pending')
        self.assertEqual(self.claim().status_code, 200)
