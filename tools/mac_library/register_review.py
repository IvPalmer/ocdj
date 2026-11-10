"""Add one verified review file to the private map and emit path-free metadata."""
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from library.snapshots import FORMATS, TIME_FORMAT, opaque_track_id, read_audio, validate_row
from tools.mac_library.helper import MAX_SOURCE, Refused, load_map, open_absolute
from tools.mac_library.reveal_map import check_private, map_lock, review_archive_path, review_fingerprint, save_map


def register(root, map_path, key_path, path, item_id, sha):
    relative = path.relative_to(root)
    if not review_archive_path(relative.parts) or any(p in ('.', '..') for p in relative.parts):
        raise Refused()
    with os.fdopen(open_absolute(str(key_path)), 'rb') as handle:
        check_private(os.fstat(handle.fileno()))
        key = handle.read(33)
    if len(key) != 32:
        raise Refused()

    with map_lock(map_path):
        mapping = load_map(map_path, str(root))
        with os.fdopen(open_absolute(str(path)), 'rb') as audio:
            info = os.fstat(audio.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SOURCE:
                raise Refused()
            if hashlib.file_digest(audio, 'sha256').hexdigest() != sha:
                raise Refused()
            audio.seek(0)
            track = read_audio(audio, FORMATS[path.suffix.lower()], info.st_size)
            fingerprint = review_fingerprint(info)
            if (not track or track.get('archive_item_id', item_id) != item_id
                    or review_fingerprint(os.fstat(audio.fileno())) != fingerprint):
                raise Refused()
            opaque = opaque_track_id(mapping['collection_id'], key, relative)
            track.update(id=opaque, archive_item_id=item_id, file_size_bytes=info.st_size)
            validate_row(track)
        mapping['tracks'][opaque] = str(relative)
        mapping.setdefault('review_fingerprints', {})[opaque] = fingerprint
        mapping['review_registered_at'] = datetime.now(timezone.utc).strftime(TIME_FORMAT)
        save_map(map_path, mapping)
    return dict(collection_id=mapping['collection_id'], track=track, sha256=sha)


def main():
    try:
        config_path, key, path, item_id, sha = sys.argv[1:]
        with os.fdopen(open_absolute(config_path), 'rb') as handle:
            info = os.fstat(handle.fileno())
            check_private(info)
            if info.st_size > 65536:
                raise Refused()
            config = json.load(handle)
        print(json.dumps(register(Path(config['root']), Path(config['map']), Path(key),
                                  Path(path), int(item_id), sha)))
    except Exception:
        print('Review indexing refused: verify the private map and delivered file.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
