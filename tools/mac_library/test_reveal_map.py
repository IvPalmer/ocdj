"""Synthetic files only; no server, DB, home inventory or credentials."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from library.snapshots import SnapshotError, scan
from reveal_map import write_reveal_map


class RevealMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'library'
        self.root.mkdir()
        self.collection = '00000000-0000-0000-0000-000000000001'
        self.key = b'k' * 32
        self.audio('nested/tone.wav')
        self.dest = self.base / 'private' / 'map.json'

    def audio(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 441)
        return path

    def mapped_scan(self):
        paths = {}
        payload, _ = scan(self.root, self.collection, self.key, local_paths=paths)
        return payload, paths

    def test_exact_scan_ids_exclusions_and_no_export_change(self):
        self.audio('ID3/staged.wav')
        self.audio('TraxDB/archived.wav')
        (self.root / 'link.wav').symlink_to(self.root / 'nested/tone.wav')
        (self.root / 'empty.wav').touch()
        payload, paths = self.mapped_scan()
        normal, _ = scan(self.root, self.collection, self.key)
        normal['captured_at'] = payload['captured_at']
        self.assertEqual(payload, normal)
        self.assertEqual(list(paths), [payload['tracks'][0]['id']])
        self.assertEqual(list(paths.values()), ['nested/tone.wav'])
        self.assertNotIn('nested', json.dumps(payload))
        write_reveal_map(self.dest, payload, self.root, paths)
        data = json.loads(self.dest.read_text())
        self.assertEqual(data['tracks'], paths)
        self.assertEqual(data['root'], str(self.root))
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.dest.parent.stat().st_mode & 0o777, 0o700)

    def test_atomic_refresh_failure_preserves_previous_map(self):
        payload, paths = self.mapped_scan()
        write_reveal_map(self.dest, payload, self.root, paths)
        old = self.dest.read_bytes()
        self.audio('another.wav')
        newer, paths = self.mapped_scan()
        with patch('reveal_map.os.replace', side_effect=OSError), self.assertRaises(OSError):
            write_reveal_map(self.dest, newer, self.root, paths)
        self.assertEqual(self.dest.read_bytes(), old)
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])
        write_reveal_map(self.dest, newer, self.root, paths)
        self.assertEqual(len(json.loads(self.dest.read_text())['tracks']), 2)

    def test_incomplete_and_mismatched_maps_refused(self):
        payload, paths = self.mapped_scan()
        incomplete = copy.deepcopy(payload)
        incomplete['complete'] = False
        for bad_payload, bad_paths in [(incomplete, paths), (payload, {})]:
            with self.assertRaises(SnapshotError):
                write_reveal_map(self.dest, bad_payload, self.root, bad_paths)
        self.assertFalse(self.dest.exists())

    def test_missing_symlink_and_escape_entries_refused(self):
        payload, paths = self.mapped_scan()
        track_id = next(iter(paths))
        (self.root / 'link.wav').symlink_to(self.root / 'nested/tone.wav')
        for relative in ['../escape.wav', '/absolute.wav', 'missing.wav', 'link.wav', 'ID3/a.wav']:
            with self.subTest(relative=relative), self.assertRaises((SnapshotError, OSError)):
                write_reveal_map(self.dest, payload, self.root, {track_id: relative})
        self.assertFalse(self.dest.exists())

    def test_symlink_or_public_destination_refused(self):
        payload, paths = self.mapped_scan()
        self.dest.parent.mkdir()
        self.dest.symlink_to(self.base / 'elsewhere')
        with self.assertRaises(SnapshotError):
            write_reveal_map(self.dest, payload, self.root, paths)
        self.dest.unlink()
        self.dest.write_text('old')
        self.dest.chmod(0o644)
        with self.assertRaises(SnapshotError):
            write_reveal_map(self.dest, payload, self.root, paths)
        self.assertEqual(self.dest.read_text(), 'old')

    def test_cli_map_only_same_identity_no_export_or_paths_in_output(self):
        # Fake home puts the known map location entirely inside the fixture.
        home = self.base / 'home'
        (home / 'Library/Containers/com.ocdj.OCDJHelperApp/Data').mkdir(parents=True)
        key = self.base / 'key'
        key.write_bytes(self.key)
        key.chmod(0o600)
        script = Path(__file__).with_name('snapshot.py')
        result = subprocess.run([sys.executable, str(script), '--root', str(self.root),
                                 '--collection-id', self.collection, '--key-file', str(key),
                                 '--local-reveal-map'], env={**os.environ, 'HOME': str(home)},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(self.base), result.stdout + result.stderr)
        maps = list(home.rglob('reveal-map.json'))
        self.assertEqual(len(maps), 1)
        self.assertEqual(json.loads(maps[0].read_text())['tracks'], self.mapped_scan()[1])
        self.assertEqual(list(self.root.iterdir()), [self.root / 'nested'])
        refused = subprocess.run([sys.executable, str(script), '--root', str(self.root), '--local-reveal-map'],
                                 capture_output=True, text=True)
        self.assertEqual(refused.returncode, 2)
        self.assertNotIn(str(self.base), refused.stdout + refused.stderr)


if __name__ == '__main__':
    unittest.main()
