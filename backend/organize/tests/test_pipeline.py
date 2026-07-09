import os
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.models import Config
from organize.models import PipelineItem
from organize.services.pipeline import (
    STAGE_FOLDERS,
    discover_and_ingest,
    ensure_pipeline_folders,
    move_item_to_stage,
    next_skippable_stage,
    process_pipeline_item,
    scan_completed_downloads,
    stage_folder_path,
    write_uploaded_file_to_downloaded,
)
from soulseek.models import Download
from wanted.models import WantedItem


class PipelineServiceTestCase(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        Config.objects.update_or_create(
            key='SOULSEEK_DOWNLOAD_ROOT',
            defaults={'value': self.tmpdir.name},
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def write_file(self, *parts, data=b'audio'):
        path = os.path.join(self.tmpdir.name, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(data)
        return path

    def test_stage_folder_creation_and_collision_safe_moves(self):
        ensure_pipeline_folders()
        for folder in STAGE_FOLDERS.values():
            self.assertTrue(os.path.isdir(os.path.join(self.tmpdir.name, folder)))

        first_path = self.write_file('01_downloaded', 'a', 'track.mp3')
        second_path = self.write_file('01_downloaded', 'b', 'track.mp3')
        first = PipelineItem.objects.create(
            original_filename='track.mp3',
            current_path=first_path,
            stage='downloaded',
        )
        second = PipelineItem.objects.create(
            original_filename='track.mp3',
            current_path=second_path,
            stage='downloaded',
        )

        move_item_to_stage(first, 'tagged')
        move_item_to_stage(second, 'tagged')

        self.assertEqual(os.path.basename(first.current_path), 'track.mp3')
        self.assertEqual(os.path.basename(second.current_path), 'track_1.mp3')
        self.assertTrue(os.path.exists(first.current_path))
        self.assertTrue(os.path.exists(second.current_path))
        self.assertEqual(first.stage, 'tagged')
        self.assertEqual(second.stage, 'tagged')

    def test_uploaded_files_use_downloaded_stage_collision_rules(self):
        first = SimpleUploadedFile('same-name.flac', b'first')
        second = SimpleUploadedFile('same-name.flac', b'second')

        first_path = write_uploaded_file_to_downloaded(first)
        second_path = write_uploaded_file_to_downloaded(second)

        self.assertEqual(os.path.dirname(first_path), stage_folder_path('downloaded'))
        self.assertEqual(os.path.basename(first_path), 'same-name.flac')
        self.assertEqual(os.path.basename(second_path), 'same-name_1.flac')
        with open(second_path, 'rb') as fh:
            self.assertEqual(fh.read(), b'second')

    def test_scan_completed_downloads_ingests_recursive_orphan_audio_only(self):
        tracked_path = self.write_file('01_downloaded', 'tracked.mp3')
        orphan_path = self.write_file('01_downloaded', '_to_triage', 'orphan.wav')
        self.write_file('01_downloaded', '_to_triage', 'notes.txt', data=b'not audio')
        PipelineItem.objects.create(
            original_filename='tracked.mp3',
            current_path=tracked_path,
            stage='downloaded',
        )

        created = scan_completed_downloads()

        self.assertEqual(created, 1)
        orphan = PipelineItem.objects.get(current_path=orphan_path)
        self.assertIsNone(orphan.download)
        self.assertEqual(orphan.stage, 'downloaded')
        self.assertEqual(PipelineItem.objects.count(), 2)

    def test_discover_and_ingest_links_wanted_download_and_new_stage_path(self):
        wanted = WantedItem.objects.create(
            artist='Urban Myths',
            title='I Just Cannot Help',
            release_name='Basement Takes',
            label='Night Shift',
            catalog_number='NS001',
            status='downloaded',
        )
        self.write_file('peer-a', 'Releases', 'Urban Myths - I Just Cannot Help.mp3')
        download = Download.objects.create(
            wanted_item=wanted,
            username='peer-a',
            filename='Releases\\Urban Myths - I Just Cannot Help.mp3',
            status='completed',
            progress=100,
        )

        item = discover_and_ingest(download.id)
        download.refresh_from_db()

        self.assertIsNotNone(item)
        self.assertEqual(item.download, download)
        self.assertEqual(item.wanted_item, wanted)
        self.assertEqual(item.artist, wanted.artist)
        self.assertEqual(item.title, wanted.title)
        self.assertEqual(item.album, wanted.release_name)
        self.assertEqual(item.stage, 'downloaded')
        self.assertEqual(download.local_path, item.current_path)
        self.assertEqual(os.path.dirname(item.current_path), stage_folder_path('downloaded'))
        self.assertTrue(os.path.exists(item.current_path))
        self.assertIsNone(discover_and_ingest(download.id))
        self.assertEqual(PipelineItem.objects.count(), 1)

    # process_pipeline_item's finally block closes DB connections — correct for its
    # real caller (a background thread after the HTTP response already returned),
    # but it kills the connection Django's TestCase reuses across the class, so it
    # must be stubbed here.
    @patch('organize.services.pipeline.db.connections.close_all')
    @patch('organize.services.converter.convert_pipeline_item')
    @patch('organize.services.renamer.rename_file')
    @patch('organize.services.tagger.tag_file')
    @patch('organize.services.agent_enrich.looks_like_garbage', return_value=False)
    # Prod compose sets OCDJ_AUTOPUBLISH=1; without this the item lands on
    # 'published' instead of 'ready' when the suite runs in a prod-env container.
    @patch.dict(os.environ, {'OCDJ_AUTOPUBLISH': '0'})
    def test_process_pipeline_item_moves_through_owned_stages(
        self,
        _looks_like_garbage,
        tag_file,
        rename_file,
        convert_pipeline_item,
        _close_all,
    ):
        wanted = WantedItem.objects.create(
            artist='Urban Myths',
            title='I Just Cannot Help',
            status='downloaded',
        )
        source_path = self.write_file('01_downloaded', 'Urban Myths - I Just Cannot Help.mp3')
        item = PipelineItem.objects.create(
            wanted_item=wanted,
            original_filename='Urban Myths - I Just Cannot Help.mp3',
            current_path=source_path,
            artist=wanted.artist,
            title=wanted.title,
            stage='downloaded',
        )

        process_pipeline_item(item.id)
        item.refresh_from_db()
        wanted.refresh_from_db()

        tag_file.assert_called_once()
        rename_file.assert_called_once()
        convert_pipeline_item.assert_called_once()
        self.assertEqual(item.stage, 'ready')
        self.assertEqual(wanted.status, 'organized')
        self.assertEqual(os.path.dirname(item.current_path), stage_folder_path('ready'))
        self.assertTrue(os.path.exists(item.current_path))
        self.assertFalse(os.path.exists(source_path))

    def test_next_skippable_stage_normalizes_working_stages(self):
        self.assertEqual(next_skippable_stage('downloaded'), 'tagged')
        self.assertEqual(next_skippable_stage('tagging'), 'renamed')
        self.assertEqual(next_skippable_stage('renaming'), 'converted')
        self.assertEqual(next_skippable_stage('converting'), 'ready')
        self.assertIsNone(next_skippable_stage('ready'))
        self.assertIsNone(next_skippable_stage('failed'))


class CleanGenreTestCase(TestCase):
    """Regression: Beatport compilation dumps overflow PipelineItem.genre (200)."""

    def test_short_genre_passes_through(self):
        from organize.services.tagger import _clean_genre
        self.assertEqual(_clean_genre('Drum & Bass'), 'Drum & Bass')

    def test_empty_genre(self):
        from organize.services.tagger import _clean_genre
        self.assertEqual(_clean_genre(''), '')
        self.assertEqual(_clean_genre(None), '')

    def test_overlong_genre_keeps_primary(self):
        from organize.services.tagger import _clean_genre, _GENRE_MAX_LEN
        blob = ('House, Deep House, Tech House, Techno (Peak Time / Driving), '
                'Afro House, Melodic House & Techno, Minimal / Deep Tech, '
                'Nu Disco / Disco, Funky / Groove / Jackin’ House, '
                'Dance / Electro Pop, Bass House, Progressive House, '
                'Drum & Bass, Trance, UK Garage / Bassline')
        self.assertGreater(len(blob), _GENRE_MAX_LEN)
        cleaned = _clean_genre(blob)
        self.assertEqual(cleaned, 'House')
        self.assertLessEqual(len(cleaned), _GENRE_MAX_LEN)

    def test_overlong_single_segment_is_truncated(self):
        from organize.services.tagger import _clean_genre, _GENRE_MAX_LEN
        cleaned = _clean_genre('x' * 300)
        self.assertEqual(len(cleaned), _GENRE_MAX_LEN)
