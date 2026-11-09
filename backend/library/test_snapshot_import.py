"""PostgreSQL fixtures for CI only; never run against a user database."""
import copy
import uuid
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from .models import LibraryTrack
from .snapshots import SnapshotError
from .snapshot_import import import_snapshot


def fixture(capture='2026-01-01T00:00:00.000000Z', count=1, collection=None):
    return dict(version=1, complete=True, collection_id=collection or str(uuid.UUID(int=1)),
                captured_at=capture, tracks=[dict(id=f'{i:064x}', artist='Fixture Artist', title='Tone (Dub)',
                format='flac', bitrate=900, sample_rate=44100, duration_seconds=6.0,
                file_size_bytes=1000) for i in range(count)])


class SnapshotImportTests(TestCase):
    def test_preview_replay_and_match_reuse_without_paths(self):
        payload = fixture()
        self.assertEqual(import_snapshot(payload)['created'], 1)
        self.assertEqual(LibraryTrack.objects.count(), 0)
        import_snapshot(payload, apply=True)
        track = LibraryTrack.objects.get()
        stamp = track.updated_at
        self.assertEqual(import_snapshot(payload, apply=True)['status'], 'replay')
        track.refresh_from_db()
        self.assertEqual(track.updated_at, stamp)
        client = APIClient()
        result = client.get('/api/library/match/', dict(artist='Fixture Artist', title='Tone (Dub)')).data
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['matches'][0]['sample_rate'], 44100)
        detail = client.get(f'/api/library/tracks/{track.pk}/').data
        self.assertEqual(detail['file_path'], '')
        self.assertEqual(detail['filename'], '')

    def test_replacement_is_collection_scoped_and_retains_missing_rows(self):
        import_snapshot(fixture(count=2), apply=True)
        other = fixture(collection=str(uuid.UUID(int=2)))
        import_snapshot(other, apply=True)
        local = LibraryTrack.objects.create(file_path='/synthetic/ready.wav', artist='Local')
        newer = fixture(capture='2026-01-02T00:00:00.000000Z')
        result = import_snapshot(newer, apply=True)
        self.assertEqual(result['missing'], 1)
        self.assertEqual(LibraryTrack.objects.count(), 4)
        self.assertEqual(LibraryTrack.objects.filter(missing=True).count(), 1)
        local.refresh_from_db()
        self.assertFalse(local.missing)
        import_snapshot(fixture('2026-01-03T00:00:00.000000Z', count=2), apply=True)
        self.assertEqual(LibraryTrack.objects.filter(missing=True).count(), 0)

    def test_stale_conflict_invalid_and_empty_never_mutate(self):
        payload = fixture()
        import_snapshot(payload, apply=True)
        newer = fixture('2026-01-02T00:00:00.000000Z')
        import_snapshot(newer, apply=True)
        conflicting = copy.deepcopy(newer)
        conflicting['tracks'][0]['bitrate'] = 100
        invalid = fixture('2026-01-03T00:00:00.000000Z', count=2)
        invalid['tracks'][-1]['file_path'] = '/private/fixture'
        for bad in [payload, conflicting, invalid, fixture(count=0)]:
            with self.subTest(bad=bad), self.assertRaises(SnapshotError):
                import_snapshot(bad, apply=True)
        self.assertEqual(LibraryTrack.objects.get().bitrate, 900)

    def test_failed_import_rolls_back_rows_and_receipt_then_retry_succeeds(self):
        from .models import MacLibrarySnapshot
        manager = LibraryTrack.objects
        original = manager.update_or_create
        calls = 0
        def failing(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('synthetic failure')
            return original(*args, **kwargs)
        payload = fixture(count=2)
        with patch.object(manager, 'update_or_create', side_effect=failing):
            with self.assertRaises(RuntimeError):
                import_snapshot(payload, apply=True)
        self.assertEqual(LibraryTrack.objects.count(), 0)
        self.assertEqual(MacLibrarySnapshot.objects.count(), 0)
        self.assertEqual(import_snapshot(payload, apply=True)['created'], 2)

    def test_mac_records_reject_all_file_operations_before_io(self):
        from .services import update_track_metadata
        from .services_promote import promote_track
        import_snapshot(fixture(), apply=True)
        track = LibraryTrack.objects.get()
        with patch('organize.services.tagger.write_tags') as write_tags, patch('library.services_promote.os.path.exists') as exists:
            with self.assertRaises(ValueError):
                update_track_metadata(track, {'title': 'Changed'})
            self.assertFalse(promote_track(track).ok)
            write_tags.assert_not_called()
            exists.assert_not_called()
        client = APIClient()
        self.assertEqual(client.patch(f'/api/library/tracks/{track.pk}/update/', {'title': 'Changed'}).status_code, 409)
        self.assertEqual(client.post(f'/api/library/tracks/{track.pk}/promote/').status_code, 409)

    def test_ready_scan_does_not_mark_mac_index_missing(self):
        import_snapshot(fixture(), apply=True)
        from .services import scan_library
        with patch('library.services._get_ready_dir', return_value='/synthetic/ready'), patch('library.services.os.path.isdir', return_value=True), patch('library.services.os.walk', return_value=[]):
            scan_library()
        self.assertFalse(LibraryTrack.objects.get().missing)
