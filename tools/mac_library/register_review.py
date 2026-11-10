"""Add one verified review file to the private map and emit path-free metadata."""
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
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


def read_request(path):
    with os.fdopen(open_absolute(str(path)), 'rb') as handle:
        info = os.fstat(handle.fileno())
        check_private(info)
        if not 0 < info.st_size <= 16384:
            raise Refused()
        return json.load(handle)


def write_reply(path, payload):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(payload).encode())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def pending_directory(inbox):
    inbox.mkdir(mode=0o700, exist_ok=True)
    fd = open_absolute(str(inbox), directory=True)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Refused()
    finally:
        os.close(fd)


def submit(inbox, request, *, timeout=150):
    """The LaunchAgent has no access to the app's Finder map sandbox."""
    pending_directory(inbox)
    with tempfile.TemporaryDirectory(prefix='delivery-', dir=inbox) as name:
        folder = Path(name)
        write_reply(folder / 'request.json', request)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                receipt = read_request(folder / 'reply.json')
            except FileNotFoundError:
                time.sleep(.25)
                continue
            if receipt:
                return receipt
            raise Refused()
    raise TimeoutError()


def process_pending(config, inbox):
    """The signed-in playback helper already owns access to the Finder map."""
    if not inbox.exists():
        return
    pending_directory(inbox)
    for folder in sorted(inbox.glob('delivery-*')):
        if folder.is_symlink() or not folder.is_dir():
            continue
        reply = folder / 'reply.json'
        if reply.exists():
            continue
        try:
            key, path, item_id, sha = read_request(folder / 'request.json')
        except FileNotFoundError:
            continue
        except (ValueError, OSError, TypeError):
            receipt = None
        else:
            try:
                receipt = register(Path(config['root']), Path(config['map']), Path(key), Path(path), int(item_id), sha)
            except Exception:
                receipt = None
        try:
            write_reply(reply, receipt)
        except FileNotFoundError:
            # The sender can time out and remove its temporary directory.
            continue


def main():
    try:
        config_path, *request = sys.argv[1:]
        print(json.dumps(submit(Path(config_path).parent / 'review-pending', request)))
    except TimeoutError:
        print('Review indexing is waiting for the signed-in Playback connector.', file=sys.stderr)
        return 1
    except Exception:
        print('Review indexing refused: verify the private map and delivered file.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
