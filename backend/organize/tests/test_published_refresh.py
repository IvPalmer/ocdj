"""Published-artifact lifecycle: the edit path that broke two live tracks.

Publishing moves a file to <publish>/<id>/ and records its sha256. Editing it
afterwards used to be PATCH + retag, which renamed the file without moving
`work_path` and rewrote the bytes without recomputing `sha256` — so the Mac
drain daemon failed with "work_path missing at claim", and would have failed
with "sha256 mismatch" even after a path fix.

The fixtures write real WAV files (stdlib `wave`) because these paths depend on
mutagen actually parsing the container.
"""

import os
import tempfile
import wave
from unittest.mock import patch

from django.test import TestCase
from mutagen.wave import WAVE

from core.models import Config
from organize.models import PipelineItem
from organize.services.publisher import compute_sha256
from organize.services.refresh import (
    RefreshError,
    refresh_published_artifact,
    resolve_artifact_path,
)
from organize.services.renamer import rename_file
from organize.services.tagger import write_tags, write_tags_atomic


def write_wav(path, frames=b'\x00\x01' * 512):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, 'wb') as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(44100)
        fh.writeframes(frames)
    return path


def read_tag(path, frame_id):
    tags = WAVE(path).tags
    if tags is None or frame_id not in tags:
        return None
    return str(tags[frame_id])


class PublishedArtifactTestCase(TestCase):
    """Base: a tmp pipeline root + publish root, and one published item."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        Config.objects.update_or_create(
            key='SOULSEEK_DOWNLOAD_ROOT',
            defaults={'value': self.tmpdir.name},
        )
        self.publish_root = os.path.join(self.tmpdir.name, '06_publish')
        self.env = patch.dict(os.environ, {'OCDJ_PUBLISH_ROOT': self.publish_root})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmpdir.cleanup()

    def make_published(self, filename='Urban Myths - Basement.wav', **kwargs):
        item = PipelineItem.objects.create(
            original_filename=filename,
            current_path='',
            final_filename=filename,
            artist='Urban Myths',
            title='Basement',
            album='Night Shift',
            stage='published',
            archive_state='on_workbench',
            sha256='0' * 64,
        )
        path = write_wav(os.path.join(self.publish_root, str(item.id), filename))
        write_tags(path, {'artist': 'Urban Myths', 'title': 'Basement',
                          'album': 'Night Shift'})
        item.current_path = path
        item.work_path = path
        item.sha256 = compute_sha256(path)
        item.archive_state = 'publishable'
        for key, value in kwargs.items():
            setattr(item, key, value)
        item.save()
        return item


class RefreshServiceTests(PublishedArtifactTestCase):
    """Item 2: one operation does tag → rename → re-hash → re-publish."""

    def test_refresh_moves_work_path_and_recomputes_hash(self):
        item = self.make_published()
        stale_sha = item.sha256
        stale_path = item.work_path

        refresh_published_artifact(item.id, metadata={'artist': 'Urban Myths',
                                                      'title': 'Attic'})
        item.refresh_from_db()

        self.assertNotEqual(item.work_path, stale_path)
        self.assertEqual(item.work_path, item.current_path)
        self.assertEqual(item.final_filename, 'Urban Myths - Attic.wav')
        self.assertTrue(os.path.exists(item.work_path))
        self.assertFalse(os.path.exists(stale_path))
        # The two failures the drain daemon reported, both gone.
        self.assertNotEqual(item.sha256, stale_sha)
        self.assertEqual(item.sha256, compute_sha256(item.work_path))
        self.assertEqual(read_tag(item.work_path, 'TIT2'), 'Attic')
        self.assertEqual(item.archive_state, 'publishable')

    def test_refresh_clears_the_failure_bookkeeping(self):
        item = self.make_published(
            archive_state='failed', drain_attempts=5,
            error_message='sha256 mismatch',
        )

        refresh_published_artifact(item.id, metadata={'title': 'Attic'})
        item.refresh_from_db()

        self.assertEqual(item.archive_state, 'publishable')
        self.assertEqual(item.drain_attempts, 0)
        self.assertEqual(item.error_message, '')
        self.assertIsNone(item.draining_until)

    def test_refresh_relocates_a_file_left_outside_the_publish_dir(self):
        item = self.make_published()
        stray = write_wav(os.path.join(self.tmpdir.name, '05_ready', 'stray.wav'))
        os.remove(item.work_path)
        PipelineItem.objects.filter(pk=item.pk).update(current_path=stray)

        refresh_published_artifact(item.id)
        item.refresh_from_db()

        self.assertEqual(
            os.path.dirname(item.work_path),
            os.path.join(self.publish_root, str(item.id)),
        )
        self.assertTrue(os.path.exists(item.work_path))

    def test_refresh_refuses_while_draining(self):
        item = self.make_published(archive_state='draining')

        with self.assertRaises(RefreshError):
            refresh_published_artifact(item.id, metadata={'title': 'Attic'})

        item.refresh_from_db()
        self.assertEqual(item.title, 'Basement')

    def test_refresh_refuses_a_workbench_item(self):
        item = PipelineItem.objects.create(
            original_filename='bench.wav',
            current_path=write_wav(os.path.join(self.tmpdir.name, '01_downloaded', 'bench.wav')),
            stage='ready',
        )

        with self.assertRaises(RefreshError):
            refresh_published_artifact(item.id)

    def test_refresh_endpoint_returns_the_updated_row(self):
        item = self.make_published()

        resp = self.client.post(
            f'/api/organize/pipeline/{item.id}/refresh/',
            data={'title': 'Attic'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['title'], 'Attic')
        self.assertEqual(body['sha256'], compute_sha256(body['work_path']))
        self.assertNotIn('claim_token', body)  # capability token, never serialised

    def test_refresh_endpoint_409s_on_a_draining_row(self):
        item = self.make_published(archive_state='draining')

        resp = self.client.post(
            f'/api/organize/pipeline/{item.id}/refresh/',
            data={'title': 'Attic'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 409)


class StateGuardTests(PublishedArtifactTestCase):
    """Item 4: draining is untouchable, archived is read-only."""

    def test_patch_refused_while_draining(self):
        item = self.make_published(archive_state='draining')

        resp = self.client.patch(
            f'/api/organize/pipeline/{item.id}/',
            data={'title': 'Attic'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.title, 'Basement')

    def test_patch_refused_on_a_published_row_and_points_at_refresh(self):
        item = self.make_published()

        resp = self.client.patch(
            f'/api/organize/pipeline/{item.id}/',
            data={'title': 'Attic'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 409)
        self.assertIn('refresh', resp.json()['hint'])
        item.refresh_from_db()
        self.assertEqual(item.title, 'Basement')

    def test_patch_still_works_on_the_workbench(self):
        item = PipelineItem.objects.create(
            original_filename='bench.wav',
            current_path='/tmp/bench.wav',
            stage='tagged',
        )

        resp = self.client.patch(
            f'/api/organize/pipeline/{item.id}/',
            data={'title': 'Attic'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.title, 'Attic')

    def test_delete_refused_while_draining_and_file_survives(self):
        item = self.make_published(archive_state='draining')

        resp = self.client.delete(f'/api/organize/pipeline/{item.id}/')

        self.assertEqual(resp.status_code, 409)
        self.assertTrue(PipelineItem.objects.filter(pk=item.pk).exists())
        self.assertTrue(os.path.exists(item.work_path))

    def test_retag_refused_on_a_published_row(self):
        item = self.make_published()
        before = item.sha256

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/retag/')

        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.sha256, before)
        self.assertEqual(compute_sha256(item.work_path), before)


class BulkMutatorGuardTests(PublishedArtifactTestCase):
    """Item 6: the fleet-wide rewrites must not touch published artifacts."""

    def test_retag_clean_skips_published_rows(self):
        item = self.make_published()
        before = compute_sha256(item.work_path)

        resp = self.client.post(
            '/api/organize/pipeline/retag-clean/',
            data={'stage': 'published'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['skipped_published'], 1)
        self.assertEqual(compute_sha256(item.work_path), before)

    def test_rerename_all_skips_published_rows(self):
        item = self.make_published()
        before = item.work_path

        resp = self.client.post(
            '/api/organize/pipeline/rerename/',
            data={'stage': 'published'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['skipped_published'], 1)
        item.refresh_from_db()
        self.assertEqual(item.work_path, before)
        self.assertTrue(os.path.exists(before))


class RenameInvariantTests(PublishedArtifactTestCase):
    """Item 5: work_path follows current_path through a rename."""

    def test_rename_moves_work_path_when_it_tracks_the_same_file(self):
        item = self.make_published()
        item.title = 'Attic'
        item.save(update_fields=['title'])

        rename_file(item)
        item.refresh_from_db()

        self.assertEqual(item.work_path, item.current_path)
        self.assertTrue(os.path.exists(item.work_path))

    def test_rename_leaves_an_unrelated_work_path_alone(self):
        item = self.make_published()
        other = write_wav(os.path.join(self.tmpdir.name, 'elsewhere', 'other.wav'))
        PipelineItem.objects.filter(pk=item.pk).update(work_path=other)
        item.refresh_from_db()
        item.title = 'Attic'
        item.save(update_fields=['title'])

        rename_file(item)
        item.refresh_from_db()

        self.assertEqual(item.work_path, other)


class AtomicTagWriteTests(PublishedArtifactTestCase):
    """Item 3: a failed tag write must leave the published bytes untouched."""

    def test_failure_leaves_the_original_bytes_intact(self):
        item = self.make_published()
        before = compute_sha256(item.work_path)

        with patch('organize.services.tagger.write_tags', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                write_tags_atomic(item.work_path, {'title': 'Attic'})

        self.assertEqual(compute_sha256(item.work_path), before)
        leftovers = [
            n for n in os.listdir(os.path.dirname(item.work_path))
            if n.startswith('.ocdj-tag-')
        ]
        self.assertEqual(leftovers, [])

    def test_success_replaces_the_file_in_one_step(self):
        item = self.make_published()

        write_tags_atomic(item.work_path, {'title': 'Attic'})

        self.assertEqual(read_tag(item.work_path, 'TIT2'), 'Attic')


class TagClearingTests(PublishedArtifactTestCase):
    """Item 10: clearing a field in the UI has to clear the embedded tag."""

    def test_empty_value_clears_the_tag(self):
        path = write_wav(os.path.join(self.tmpdir.name, 'clear.wav'))
        write_tags(path, {'artist': 'Urban Myths', 'title': 'Basement',
                          'album': 'Night Shift', 'genre': 'House'})
        self.assertEqual(read_tag(path, 'TALB'), 'Night Shift')

        write_tags(path, {'artist': 'Urban Myths', 'title': 'Basement',
                          'album': '', 'genre': ''})

        self.assertIsNone(read_tag(path, 'TALB'))
        self.assertIsNone(read_tag(path, 'TCON'))
        self.assertEqual(read_tag(path, 'TIT2'), 'Basement')

    def test_absent_key_leaves_the_tag_alone(self):
        path = write_wav(os.path.join(self.tmpdir.name, 'keep.wav'))
        write_tags(path, {'artist': 'Urban Myths', 'title': 'Basement',
                          'album': 'Night Shift'})

        write_tags(path, {'title': 'Attic'})

        self.assertEqual(read_tag(path, 'TALB'), 'Night Shift')
        self.assertEqual(read_tag(path, 'TIT2'), 'Attic')

    def test_refresh_clears_a_field_the_operator_emptied(self):
        item = self.make_published()

        refresh_published_artifact(item.id, metadata={'album': ''})
        item.refresh_from_db()

        self.assertEqual(item.album, '')
        self.assertIsNone(read_tag(item.work_path, 'TALB'))


class DownloadFallbackTests(PublishedArtifactTestCase):
    """Item 8: a broken row is exactly the one you need to download."""

    def test_stale_work_path_falls_back_to_current_path(self):
        item = self.make_published()
        real = item.work_path
        PipelineItem.objects.filter(pk=item.pk).update(
            work_path=os.path.join(os.path.dirname(real), 'gone.wav'),
        )
        item.refresh_from_db()

        self.assertEqual(resolve_artifact_path(item), real)

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/download-url/')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['filename'], os.path.basename(real))

    def test_failed_items_can_still_be_downloaded(self):
        item = self.make_published(archive_state='failed')

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/download-url/')

        self.assertEqual(resp.status_code, 200)
        streamed = self.client.get(resp.json()['url'])
        self.assertEqual(streamed.status_code, 200)


class RetryDrainTests(PublishedArtifactTestCase):
    """Item 9: retry verifies the artifact instead of replaying the failure."""

    def test_retry_refuses_when_the_file_is_gone(self):
        item = self.make_published(archive_state='failed')
        os.remove(item.work_path)
        PipelineItem.objects.filter(pk=item.pk).update(current_path='')

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/retry-drain/')

        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'failed')

    def test_retry_refuses_when_the_bytes_no_longer_match(self):
        item = self.make_published(archive_state='failed')
        write_wav(item.work_path, frames=b'\x02\x03' * 512)

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/retry-drain/')

        self.assertEqual(resp.status_code, 409)
        self.assertIn('sha256', resp.json()['error'])
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'failed')

    def test_retry_requeues_a_sound_artifact(self):
        item = self.make_published(
            archive_state='failed', drain_attempts=5, error_message='rsync failed',
        )

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/retry-drain/')

        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.archive_state, 'publishable')
        self.assertEqual(item.drain_attempts, 0)
        self.assertEqual(item.error_message, '')

    def test_retry_repairs_a_stale_work_path(self):
        item = self.make_published(archive_state='failed')
        real = item.work_path
        PipelineItem.objects.filter(pk=item.pk).update(
            work_path=os.path.join(os.path.dirname(real), 'gone.wav'),
        )

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/retry-drain/')

        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.work_path, real)
