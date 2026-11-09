"""Synthetic PostgreSQL/filesystem fixtures; run in CI, never the local API."""
import os
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import Config
from organize.models import PipelineItem


class PlaybackFixture:
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloads = self.root / 'downloads'
        self.publish = self.root / 'publish'
        self.downloads.mkdir()
        self.publish.mkdir()
        Config.objects.update_or_create(key='SOULSEEK_DOWNLOAD_ROOT', defaults={'value': str(self.downloads)})
        self.env = patch.dict(os.environ, {'OCDJ_PUBLISH_ROOT': str(self.publish)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = self.downloads / 'tone.wav'
        with wave.open(str(self.path), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b'\0\1' * 8000 * 30)
        self.item = PipelineItem.objects.create(original_filename='tone.wav',
            current_path=str(self.path), artist='Fixture artist', title='Pipeline tone', stage='ready')
        self.url = f'/api/organize/pipeline/{self.item.pk}/audio/'


class PipelinePlaybackTests(PlaybackFixture, TestCase):
    def test_normal_entry_read_only_range_and_head(self):
        before = PipelineItem.objects.values().get(pk=self.item.pk)
        listing = self.client.get('/api/organize/pipeline/').json()['results'][0]
        self.assertEqual(listing['playback_url'], self.url)
        self.assertEqual(listing['playback_unavailable'], '')
        self.assertEqual(self.client.get(f'/api/organize/pipeline/{self.item.pk}/').json()['playback_url'], self.url)
        raw = self.path.read_bytes()
        for value, start, end in [(None, 0, len(raw)-1), ('bytes=4-7', 4, 7),
                                  ('bytes=-3', len(raw)-3, len(raw)-1),
                                  ('bytes=100-', 100, len(raw)-1), ('bytes=1-9999999', 1, len(raw)-1)]:
            headers = {'HTTP_RANGE': value} if value else {}
            response = self.client.get(self.url, **headers)
            self.assertEqual(response.status_code, 206 if value else 200)
            self.assertEqual(b''.join(response.streaming_content), raw[start:end+1])
            self.assertEqual(int(response['Content-Length']), end-start+1)
            self.assertEqual(response['Accept-Ranges'], 'bytes')
            self.assertEqual(response['Content-Disposition'], 'inline')
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            if value:
                self.assertEqual(response['Content-Range'], f'bytes {start}-{end}/{len(raw)}')
        head = self.client.head(self.url, HTTP_RANGE='bytes=4-7')
        self.assertEqual(head.status_code, 206)
        self.assertEqual(head.content, b'')
        self.assertEqual(head['Content-Length'], '4')
        self.assertEqual(head['Content-Type'], 'audio/wav')
        for value in ['bytes=-0', 'bytes=7-4', 'bytes=1-2,4-5', 'bytes=-', 'nope',
                      'bytes=' + '9'*500 + '-', f'bytes={len(raw)}-']:
            response = self.client.get(self.url, HTTP_RANGE=value)
            self.assertEqual(response.status_code, 416)
            self.assertEqual(response['Content-Range'], f'bytes */{len(raw)}')
        self.assertEqual(PipelineItem.objects.values().get(pk=self.item.pk), before)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)
        self.assertEqual(self.client.get('/api/organize/pipeline/999999/audio/').status_code, 404)

    def unavailable(self):
        listing = self.client.get(f'/api/organize/pipeline/{self.item.pk}/').json()
        self.assertIsNone(listing['playback_url'])
        self.assertTrue(listing['playback_unavailable'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 410)
        self.assertNotIn(str(self.root), str(response.content))

    def test_missing_archived_and_changed_after_queue(self):
        self.assertEqual(self.client.get('/api/organize/pipeline/').json()['results'][0]['playback_url'], self.url)
        self.path.unlink()
        self.unavailable()
        self.path.write_bytes(b'fixture')
        PipelineItem.objects.filter(pk=self.item.pk).update(archive_state='archived',
            archived_at=timezone.now(), music_persistent_id='fixture', sha256='a'*64)
        self.unavailable()

    def test_resolver_fallback_and_separate_publish_root(self):
        target = self.publish / 'published.wav'
        self.path.rename(target)
        self.item.current_path = str(target)
        self.item.work_path = str(self.downloads / 'stale.wav')
        self.item.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), target.read_bytes())

    def test_roots_symlinks_traversal_and_file_types(self):
        outside = self.root / 'outside.wav'
        outside.write_bytes(b'private')
        link = self.downloads / 'link.wav'
        link.symlink_to(outside)
        inside_link = self.downloads / 'inside.wav'
        inside_link.symlink_to(self.path)
        directory_link = self.downloads / 'linked'
        directory_link.symlink_to(self.downloads, target_is_directory=True)
        fifo = self.downloads / 'pipe.wav'
        os.mkfifo(fifo)
        directory = self.downloads / 'dir.wav'
        directory.mkdir()
        empty = self.downloads / 'empty.wav'
        empty.touch()
        html = self.downloads / 'page.html'
        html.write_text('<html>not audio</html>')
        sibling = self.root / 'downloads-other'
        sibling.mkdir()
        (sibling / 'tone.wav').write_bytes(b'private')
        for path in [outside, link, inside_link, directory_link / 'tone.wav', fifo,
                     directory, empty, html, sibling / 'tone.wav',
                     str(self.downloads) + '/../outside.wav', 'relative.wav']:
            with self.subTest(path=path):
                PipelineItem.objects.filter(pk=self.item.pk).update(current_path=str(path))
                self.unavailable()
        # A caller-supplied path cannot select different bytes.
        PipelineItem.objects.filter(pk=self.item.pk).update(current_path=str(self.path))
        response = self.client.get(self.url, {'path': str(outside)})
        self.assertEqual(b''.join(response.streaming_content), self.path.read_bytes())
