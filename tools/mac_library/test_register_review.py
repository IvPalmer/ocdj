from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
import wave

from mutagen.id3 import GRP1
from mutagen.wave import WAVE

from tools.mac_library.helper import Refused, load_map, source
from tools.mac_library.register_review import process_pending, register, submit


class ReviewRegisterTests(unittest.TestCase):
    def test_checks_archive_bytes_and_adds_a_fingerprinted_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            archive = root / 'ID3/_review/fixture.wav'
            archive.parent.mkdir(parents=True)
            with wave.open(str(archive), 'wb') as audio:
                audio.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\x00\x01' * 512)
            tags = WAVE(archive)
            tags.add_tags()
            tags.tags.add(GRP1(encoding=3, text=['ocdj:246']))
            tags.save()
            sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            key = root / 'key'
            key.write_bytes(b'x' * 32)
            key.chmod(0o600)
            mapping = root / 'map'
            collection = '00000000-0000-0000-0000-000000000001'
            mapping.write_text(json.dumps(dict(version=1, collection_id=collection, root=str(root),
                                               tracks={'0'*64: 'existing.wav'})))
            mapping.chmod(0o600)
            before = mapping.read_bytes()
            for item, digest in [(246, '0'*64), (247, sha)]:
                with self.assertRaises(Refused):
                    register(root, mapping, key, archive, item, digest)
                self.assertEqual(mapping.read_bytes(), before)
            inbox = root / 'review-pending'
            with ThreadPoolExecutor(1) as agent:
                request = agent.submit(submit, inbox, [str(key), str(archive), '246', sha], timeout=3)
                for _ in range(100):
                    if list(inbox.glob('delivery-*/request.json')):
                        break
                    time.sleep(.02)
                self.assertEqual(mapping.read_bytes(), before)
                process_pending({'root': str(root), 'map': str(mapping)}, inbox)
                data = request.result()
            self.assertEqual(list(inbox.iterdir()), [])
            opaque = data['track']['id']
            self.assertEqual(data['track']['archive_item_id'], 246)
            self.assertNotIn(str(root), json.dumps(data))
            updated = load_map(mapping, str(root))
            self.assertEqual(updated['tracks']['0'*64], 'existing.wav')
            with source(updated, collection, opaque) as (_fd, format_):
                self.assertEqual(format_, 'wav')
            archive.write_bytes(b'changed')
            with self.assertRaises(Refused), source(updated, collection, opaque):
                pass
