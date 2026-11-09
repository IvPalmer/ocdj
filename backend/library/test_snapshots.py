"""Offline fixtures: no Django setup, DB, API, or user metadata."""
import copy
import json
import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

from library import snapshots


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.collection = str(uuid.UUID(int=1))
        self.key = b'k' * 32

    def audio(self, name='tone.wav'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 441)
        return path

    def scan(self):
        return snapshots.scan(self.root, self.collection, self.key)

    def test_read_only_path_free_stable_ids_and_real_quality(self):
        path = self.audio()
        before = path.read_bytes()
        first, counts = self.scan()
        second, _ = self.scan()
        row = first['tracks'][0]
        self.assertEqual(row['id'], second['tracks'][0]['id'])
        self.assertEqual(row['sample_rate'], 44100)
        self.assertEqual(row['bitrate'], 705)
        self.assertEqual(counts['readable'], 1)
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn(str(self.root), json.dumps(first))
        self.assertNotIn('tone.wav', json.dumps(first))
        snapshots.validate(first)

    def test_staging_and_symlinks_are_not_library(self):
        self.audio()
        self.audio('ID3/staged.wav')
        self.audio('archive/TraxDB/archive.wav')
        (self.root / 'link.wav').symlink_to(self.root / 'tone.wav')
        (self.root / 'linked').symlink_to(self.root / 'archive', target_is_directory=True)
        payload, counts = self.scan()
        self.assertEqual(len(payload['tracks']), 1)
        self.assertEqual(counts['excluded_staging_dirs'], 2)
        self.assertEqual(counts['skipped_symlinks'], 2)
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.scan(self.root / 'ID3', self.collection, self.key)

    def test_empty_and_tag_only_placeholders_are_excluded(self):
        from mutagen.id3 import ID3, TPE1
        self.audio()
        (self.root / 'empty.aiff').touch()
        tags = ID3()
        tags.add(TPE1(encoding=3, text=['Fixture']))
        path = self.root / 'tags-only.mp3'
        tags.save(path, padding=lambda _: 0)
        with path.open('ab') as handle:
            handle.write(bytes(2))
        payload, counts = self.scan()
        self.assertTrue(payload['complete'])
        self.assertEqual(counts['excluded_non_audio'], 2)
        self.assertEqual(len(payload['tracks']), 1)
        with path.open('ab') as handle:
            handle.write(bytes(2))
        payload, counts = self.scan()
        self.assertFalse(payload['complete'])
        self.assertEqual(counts['read_errors'], 1)

    def test_failures_make_export_incomplete_without_disclosing_paths(self):
        (self.root / 'private-broken.mp3').write_bytes(b'broken')
        payload, counts = self.scan()
        self.assertFalse(payload['complete'])
        self.assertEqual(counts['read_errors'], 1)
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.validate(payload)
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.write_snapshot(self.root / 'out.json', payload)
        self.assertFalse((self.root / 'out.json').exists())

    def test_walk_failure_is_not_an_empty_complete_snapshot(self):
        def failed_walk(*args, **kwargs):
            kwargs['onerror'](PermissionError('secret path'))
            return iter(())
        with patch('library.snapshots.os.walk', failed_walk):
            payload, counts = self.scan()
        self.assertFalse(payload['complete'])
        self.assertEqual(counts['walk_errors'], 1)

    def test_changed_file_is_not_exportable(self):
        path = self.audio()
        original = snapshots.read_metadata
        def changed(handle, fmt):
            result = original(handle, fmt)
            with path.open('ab') as dest:
                dest.write(b'changed')
            return result
        with patch('library.snapshots.read_metadata', changed):
            payload, counts = self.scan()
        self.assertFalse(payload['complete'])
        self.assertEqual(counts['read_errors'], 1)

    def test_strict_validation_rejects_duplicates_unknown_fields_and_invalid_quality(self):
        self.audio()
        payload, _ = self.scan()
        bad_payloads = []
        bad = copy.deepcopy(payload)
        bad['tracks'].append(bad['tracks'][0])
        bad_payloads.append(bad)
        for key, value in [('file_path', '/private/fixture.wav'), ('sample_rate', True),
                           ('bitrate', -1), ('duration_seconds', float('nan')),
                           ('artist', 'x' * 501), ('id', '../escape')]:
            bad = copy.deepcopy(payload)
            bad['tracks'][0][key] = value
            bad_payloads.append(bad)
        for bad in bad_payloads:
            with self.subTest(bad=bad), self.assertRaises(snapshots.SnapshotError):
                snapshots.validate(bad)

    def test_private_export_never_overwrites_and_bounded_load(self):
        self.audio()
        payload, _ = self.scan()
        dest = self.root / 'snapshot.json'
        snapshots.write_snapshot(dest, payload)
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual(snapshots.load_snapshot(dest), payload)
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.write_snapshot(dest, payload)
        dest.write_text('{"version":1,"version":2}')
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.load_snapshot(dest)

    def test_replay_stale_and_conflicting_capture(self):
        self.audio()
        payload, _ = self.scan()
        digest = snapshots.digest(payload)
        self.assertEqual(snapshots.replay_status(payload, None, ''), 'new')
        self.assertEqual(snapshots.replay_status(payload, payload['captured_at'], digest), 'replay')
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.replay_status(payload, payload['captured_at'], 'different')
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.replay_status(payload, '2099-01-01T00:00:00.000000Z', 'different')

    def test_wav_id3_identity_does_not_fall_back_to_filename(self):
        from mutagen.wave import WAVE
        from mutagen.id3 import TPE1, TIT2
        path = self.audio('misleading-name.wav')
        audio = WAVE(path)
        audio.add_tags()
        audio.tags.add(TPE1(encoding=3, text=['Fixture Artist']))
        audio.tags.add(TIT2(encoding=3, text=['Tone (Dub)']))
        audio.save()
        payload, counts = self.scan()
        self.assertEqual(counts['tagged_identity'], 1)
        self.assertEqual(payload['tracks'][0]['artist'], 'Fixture Artist')
        self.assertEqual(payload['tracks'][0]['title'], 'Tone (Dub)')

    def test_snapshot_carries_only_unambiguous_organize_grouping_ids(self):
        from mutagen.id3 import GRP1
        from mutagen.wave import WAVE

        for filename, texts in [('tagged.wav', ['ocdj:123']),
                                ('unrelated.wav', ['personal grouping']),
                                ('ambiguous.wav', ['ocdj:234', 'ocdj:345'])]:
            path = self.audio(filename)
            audio = WAVE(path)
            audio.add_tags()
            audio.tags.add(GRP1(encoding=3, text=texts))
            audio.save()
        payload, _ = self.scan()
        snapshots.validate(payload)
        self.assertEqual([row['archive_item_id'] for row in payload['tracks']
                          if 'archive_item_id' in row], [123])
        self.assertNotIn('personal grouping', json.dumps(payload))

    def test_size_cap_and_bad_envelopes(self):
        self.audio()
        payload, _ = self.scan()
        for field, value in [('version', True), ('collection_id', 'not-uuid'),
                             ('captured_at', '2099-01-01T00:00:00.000000Z'),
                             ('tracks', []), ('complete', False)]:
            bad = copy.deepcopy(payload)
            bad[field] = value
            with self.assertRaises(snapshots.SnapshotError):
                snapshots.validate(bad)
        path = self.root / 'oversized.json'
        path.write_bytes(b' ' * 1025)
        with patch('library.snapshots.MAX_BYTES', 1024), self.assertRaises(snapshots.SnapshotError):
            snapshots.load_snapshot(path)

    def test_cli_dry_run_and_failure_output_are_aggregate_only(self):
        import subprocess
        import sys
        self.audio()
        script = Path(__file__).resolve().parents[2] / 'tools/mac_library/snapshot.py'
        args = [sys.executable, str(script), '--root', str(self.root)]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = json.loads(result.stdout)
        self.assertEqual(counts['mode'], 'dry_run')
        self.assertEqual(counts['readable'], 1)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        self.assertEqual(list(self.root.iterdir()), [self.root / 'tone.wav'])
        result = subprocess.run(args + ['--export', str(self.root / 'private.json')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        self.assertFalse((self.root / 'private.json').exists())
