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

        summary = scan_completed_downloads()

        self.assertEqual(summary['created'], 1)
        self.assertEqual(summary['created_from_filesystem'], 1)
        self.assertEqual(summary['errors'], [])
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


def make_archived(**kwargs):
    """Build an archived PipelineItem that satisfies the model check constraints.

    'archived' means the VPS bytes are gone and Music.app on the Mac holds the
    only copy, so work_path must be empty while sha256 / persistent id /
    archived_at are all set.
    """
    from django.utils import timezone

    defaults = dict(
        original_filename='drained.mp3',
        current_path='',
        stage='published',
        archive_state='archived',
        work_path='',
        sha256='0' * 64,
        music_persistent_id='ABCDEF0123456789',
        archived_at=timezone.now(),
    )
    defaults.update(kwargs)
    return PipelineItem.objects.create(**defaults)


class PipelineStatsTestCase(TestCase):
    """Regression: 'published' was missing from the stats response.

    All 129 live rows sat at stage='published', so every card read 0 while
    `total` read 129 and the module looked broken.
    """

    def test_every_model_stage_is_reported(self):
        resp = self.client.get('/api/organize/pipeline/stats/')

        self.assertEqual(resp.status_code, 200)
        for stage, _label in PipelineItem.STAGE_CHOICES:
            self.assertIn(stage, resp.json(), f'stats response dropped stage {stage!r}')

    def test_published_items_are_counted_and_total_reconciles(self):
        make_archived()
        make_archived()
        PipelineItem.objects.create(
            original_filename='pending.mp3',
            current_path='/tmp/pending.mp3',
            stage='downloaded',
        )

        data = self.client.get('/api/organize/pipeline/stats/').json()

        self.assertEqual(data['published'], 2)
        self.assertEqual(data['downloaded'], 1)
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['archived'], 2)
        # The bug's signature: stage keys summing to less than `total`.
        stage_sum = sum(data[stage] for stage, _ in PipelineItem.STAGE_CHOICES)
        self.assertEqual(stage_sum, data['total'])

    def test_stage_outside_the_model_vocabulary_still_reconciles(self):
        # choices are not a DB constraint, so a legacy/garbled stage can exist.
        # It must not inflate `total` while hiding from every key.
        item = PipelineItem.objects.create(
            original_filename='legacy.mp3',
            current_path='/tmp/legacy.mp3',
            stage='downloaded',
        )
        PipelineItem.objects.filter(pk=item.pk).update(stage='from_2019')

        data = self.client.get('/api/organize/pipeline/stats/').json()

        self.assertEqual(data['total'], 1)
        self.assertEqual(data['from_2019'], 1)
        self.assertEqual(sum(v for k, v in data.items() if k not in ('total', 'archived')), data['total'])


class PipelineListArchiveTestCase(TestCase):
    """Archived filtering + the archived tally belong to the server.

    Doing them client-side made both page-local: 129 rows rendered as an
    empty table with "Show archived (50)".
    """

    def _mk_workbench(self, n):
        for i in range(n):
            PipelineItem.objects.create(
                original_filename=f'track-{i}.mp3',
                current_path=f'/tmp/track-{i}.mp3',
                stage='downloaded',
            )

    def test_archived_excluded_by_default_with_global_count(self):
        self._mk_workbench(2)
        make_archived()
        make_archived()
        make_archived()

        data = self.client.get('/api/organize/pipeline/').json()

        self.assertEqual(data['count'], 2)
        self.assertEqual(len(data['results']), 2)
        self.assertEqual(data['archived_count'], 3)
        self.assertTrue(all(i['archive_state'] != 'archived' for i in data['results']))

    def test_include_archived_returns_them(self):
        self._mk_workbench(1)
        make_archived()

        data = self.client.get('/api/organize/pipeline/?include_archived=1').json()

        self.assertEqual(data['count'], 2)
        self.assertEqual(data['archived_count'], 1)

    def test_archived_count_is_not_page_local(self):
        # 55 workbench rows fill page 1 and spill onto page 2; the archived
        # rows are ordered last by -created, so a page-local tally would
        # report 0 archived on page 1 and hide the toggle entirely.
        self._mk_workbench(55)
        for _ in range(4):
            make_archived()

        page1 = self.client.get('/api/organize/pipeline/').json()
        page2 = self.client.get('/api/organize/pipeline/?page=2').json()

        self.assertEqual(page1['count'], 55)
        self.assertEqual(len(page1['results']), 50)
        self.assertIsNotNone(page1['next'])
        self.assertEqual(page1['archived_count'], 4)
        self.assertEqual(page1['page_size'], 50)
        # Rows 51-55 are reachable — they used to be unreachable entirely.
        self.assertEqual(len(page2['results']), 5)
        self.assertEqual(page2['archived_count'], 4)

    def test_archived_count_respects_the_stage_filter(self):
        self._mk_workbench(1)
        make_archived()

        data = self.client.get('/api/organize/pipeline/?stage=downloaded').json()

        self.assertEqual(data['count'], 1)
        self.assertEqual(data['archived_count'], 0)


class PipelineScanSummaryTestCase(TestCase):
    """`created: 0` alone can't tell a healthy scan from a broken one."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        Config.objects.update_or_create(
            key='SOULSEEK_DOWNLOAD_ROOT',
            defaults={'value': self.tmpdir.name},
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_idle_pipeline_reports_nothing_new_not_a_failure(self):
        resp = self.client.post('/api/organize/pipeline/scan/')

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['created'], 0)
        self.assertEqual(data['already_tracked'], 0)
        self.assertEqual(data['missing_files'], 0)
        self.assertEqual(data['error_count'], 0)
        self.assertIn('nothing new', data['message'])

    def test_already_tracked_downloads_are_distinguished(self):
        download = Download.objects.create(
            username='peer-a',
            filename='Releases\\already.mp3',
            status='completed',
            progress=100,
        )
        PipelineItem.objects.create(
            download=download,
            original_filename='already.mp3',
            current_path=os.path.join(self.tmpdir.name, '01_downloaded', 'already.mp3'),
            stage='downloaded',
        )

        data = self.client.post('/api/organize/pipeline/scan/').json()

        self.assertEqual(data['created'], 0)
        self.assertEqual(data['already_tracked'], 1)
        self.assertEqual(data['missing_files'], 0)
        self.assertIn('already tracked', data['message'])

    def test_download_whose_file_vanished_is_reported_as_missing(self):
        Download.objects.create(
            username='peer-a',
            filename='Releases\\gone.mp3',
            status='completed',
            progress=100,
        )

        data = self.client.post('/api/organize/pipeline/scan/').json()

        self.assertEqual(data['created'], 0)
        self.assertEqual(data['missing_files'], 1)
        self.assertIn('missing on disk', data['message'])

    def test_ingest_crash_is_surfaced_not_swallowed(self):
        Download.objects.create(
            username='peer-a',
            filename='Releases\\boom.mp3',
            status='completed',
            progress=100,
        )

        with patch('organize.services.pipeline.discover_and_ingest',
                   side_effect=RuntimeError('disk on fire')):
            data = self.client.post('/api/organize/pipeline/scan/').json()

        self.assertEqual(data['created'], 0)
        self.assertEqual(data['error_count'], 1)
        self.assertIn('disk on fire', data['errors'][0]['error'])
        self.assertIn('errored', data['message'])

    def test_mixed_outcome_names_every_category_not_just_a_headline(self):
        # A run that both created and failed must not read as an unqualified
        # success, and one error among many tracked rows is not "all errored".
        for name in ('boom.mp3', 'ok.mp3'):
            Download.objects.create(
                username='peer-a', filename=f'Releases\\{name}',
                status='completed', progress=100,
            )
        tracked = Download.objects.create(
            username='peer-b', filename='Releases\\known.mp3',
            status='completed', progress=100,
        )
        PipelineItem.objects.create(
            download=tracked, original_filename='known.mp3',
            current_path='/nowhere/known.mp3', stage='downloaded',
        )
        os.makedirs(os.path.join(self.tmpdir.name, '01_downloaded'), exist_ok=True)
        with open(os.path.join(self.tmpdir.name, '01_downloaded', 'orphan.mp3'), 'wb') as fh:
            fh.write(b'audio')

        with patch('organize.services.pipeline.discover_and_ingest',
                   side_effect=RuntimeError('boom')):
            data = self.client.post('/api/organize/pipeline/scan/').json()

        self.assertEqual(data['created'], 1)          # the filesystem orphan
        self.assertEqual(data['already_tracked'], 1)
        self.assertEqual(data['error_count'], 2)
        for fragment in ('1 created', '1 already tracked', '2 errored'):
            self.assertIn(fragment, data['message'])


class PipelineSkipTestCase(TestCase):
    """Skip advanced item.stage without moving the file — DB and disk desynced."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        Config.objects.update_or_create(
            key='SOULSEEK_DOWNLOAD_ROOT',
            defaults={'value': self.tmpdir.name},
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_skip_moves_the_file_into_the_new_stage_folder(self):
        ensure_pipeline_folders()
        path = os.path.join(stage_folder_path('downloaded'), 'x.mp3')
        with open(path, 'wb') as fh:
            fh.write(b'audio')
        item = PipelineItem.objects.create(
            original_filename='x.mp3', current_path=path, stage='downloaded',
        )

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/skip/')
        item.refresh_from_db()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(item.stage, 'tagged')
        self.assertEqual(os.path.dirname(item.current_path), stage_folder_path('tagged'))
        self.assertTrue(os.path.exists(item.current_path))
        self.assertFalse(os.path.exists(path))
        self.assertNotIn('warning', resp.json())

    def test_skip_without_bytes_on_the_workbench_is_refused(self):
        # Advancing the row here would recreate the very DB/disk divergence
        # this endpoint was fixed to stop producing, so it must refuse.
        path = os.path.join(self.tmpdir.name, '01_downloaded', 'gone.mp3')
        item = PipelineItem.objects.create(
            original_filename='gone.mp3', current_path=path, stage='downloaded',
        )

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/skip/')
        item.refresh_from_db()

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(item.stage, 'downloaded')
        self.assertEqual(item.current_path, path)

    def test_skip_is_refused_while_a_worker_owns_the_file(self):
        # Skip moves bytes now, so honouring it mid-tag would yank the file out
        # from under the running worker.
        ensure_pipeline_folders()
        path = os.path.join(stage_folder_path('downloaded'), 'busy.mp3')
        with open(path, 'wb') as fh:
            fh.write(b'audio')
        item = PipelineItem.objects.create(
            original_filename='busy.mp3', current_path=path, stage='tagging',
        )

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/skip/')
        item.refresh_from_db()

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(item.stage, 'tagging')
        self.assertTrue(os.path.exists(path))

    def test_skip_from_a_terminal_stage_still_rejected(self):
        item = PipelineItem.objects.create(
            original_filename='done.mp3', current_path='', stage='ready',
        )

        resp = self.client.post(f'/api/organize/pipeline/{item.id}/skip/')

        self.assertEqual(resp.status_code, 400)


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


class CleanReleaseMetadataTestCase(TestCase):
    def test_release_annotation_and_embedded_track_number_are_removed(self):
        from organize.services.renamer import clean_album, clean_title

        self.assertEqual(
            clean_title('Natural Habitat (vinyl available) - 01 Congo River'),
            'Congo River',
        )
        self.assertEqual(clean_album('Natural Habitat (vinyl available)'), 'Natural Habitat')

    def test_musical_parenthetical_is_preserved(self):
        from organize.services.renamer import clean_title

        self.assertEqual(clean_title('Congo River (Dub Mix)'), 'Congo River (Dub Mix)')
        self.assertEqual(clean_title('Congo River (Original Mix)'), 'Congo River (Original Mix)')
        self.assertEqual(clean_title('Congo River (Radio Edit)'), 'Congo River (Radio Edit)')


class PipelineDeleteTestCase(TestCase):
    """DELETE removes the row and any files still inside OCDJ-owned roots."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        Config.objects.update_or_create(
            key='SOULSEEK_DOWNLOAD_ROOT',
            defaults={'value': self.tmpdir.name},
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def _mk_item(self, path):
        return PipelineItem.objects.create(
            original_filename=os.path.basename(path),
            current_path=path,
            stage='downloaded',
        )

    def test_delete_removes_row_and_workbench_file(self):
        path = os.path.join(self.tmpdir.name, '01_downloaded', 'x.mp3')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(b'audio')
        item = self._mk_item(path)

        resp = self.client.delete(f'/api/organize/pipeline/{item.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(PipelineItem.objects.filter(id=item.id).exists())
        self.assertFalse(os.path.exists(path))

    def test_delete_with_missing_file_still_removes_row(self):
        path = os.path.join(self.tmpdir.name, '01_downloaded', 'gone.mp3')
        item = self._mk_item(path)

        resp = self.client.delete(f'/api/organize/pipeline/{item.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(PipelineItem.objects.filter(id=item.id).exists())

    def test_delete_never_touches_files_outside_roots(self):
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as fh:
            fh.write(b'audio')
            outside = fh.name
        try:
            item = self._mk_item(outside)
            resp = self.client.delete(f'/api/organize/pipeline/{item.id}/')
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(PipelineItem.objects.filter(id=item.id).exists())
            self.assertTrue(os.path.exists(outside))  # untouched
        finally:
            if os.path.exists(outside):
                os.unlink(outside)
