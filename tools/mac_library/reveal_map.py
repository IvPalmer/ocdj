"""Private Mac-only sidecar. Never part of the server snapshot payload."""
from contextlib import contextmanager
import fcntl
import json
import os
import stat
import tempfile
from pathlib import Path


def default_map_path():
    return (Path.home() / 'Library/Containers/com.ocdj.OCDJHelperApp/Data/Library/'
            'Application Support/OCDJ/reveal-map.json')


def review_archive_path(parts):
    return len(parts) == 3 and tuple(part.casefold() for part in parts[:2]) == ('id3', '_review')


def review_fingerprint(info):
    return [str(value) for value in (info.st_dev, info.st_ino, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns)]


def write_reveal_map(destination, payload, root, paths):
    """Replace only the local sidecar, atomically, after a complete validated scan."""
    # Playback needs only default_map_path; snapshot dependencies stay with export.
    from library.snapshots import EXCLUDED, FORMATS, SnapshotError, read_audio, validate

    validate(payload)
    root = Path(root).expanduser().resolve(strict=True)
    if set(paths) != {row['id'] for row in payload['tracks']}:
        raise SnapshotError('Local map does not match the complete scan.')
    archive_ids = {row['id']: row['archive_item_id'] for row in payload['tracks'] if row.get('archive_item_id')}
    fingerprints = {}
    for opaque, relative in paths.items():
        parts = Path(relative).parts
        if (not parts or Path(relative).is_absolute() or
                any(p in ('.', '..') for p in parts) or
                (any(p.casefold() in EXCLUDED for p in parts) and
                 not (review_archive_path(parts) and opaque in archive_ids))):
            raise SnapshotError('Invalid local map entry.')
        candidate = root / relative
        if candidate.resolve(strict=True) != candidate or not candidate.is_file():
            raise SnapshotError('Local map file changed; rescan the library.')
        if review_archive_path(parts):
            fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as handle:
                before = os.fstat(handle.fileno())
                fingerprint = review_fingerprint(before)
                metadata = read_audio(handle, FORMATS[candidate.suffix.lower()], before.st_size)
                if (not metadata or metadata.get('archive_item_id') != archive_ids[opaque] or
                        fingerprint != review_fingerprint(os.fstat(handle.fileno()))):
                    raise SnapshotError('Review archive changed; rescan the library.')
                fingerprints[opaque] = fingerprint
    destination = Path(destination)
    try:
        with map_lock(destination):
            if destination.exists():
                with destination.open('rb') as handle:
                    old = json.load(handle)
                if old.get('review_registered_at', '') >= payload['captured_at']:
                    raise SnapshotError('A review delivery changed the map during the scan. Rescan the library.')
            save_map(destination, dict(version=1, collection_id=payload['collection_id'],
                                       root=str(root), tracks=paths, review_fingerprints=fingerprints))
    except ValueError as error:
        raise SnapshotError('Could not replace the private local map. Rescan the library.') from error


@contextmanager
def map_lock(destination):
    """Serialize full snapshot and incremental writes on a stable lock inode."""
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.parent.resolve(strict=True) != destination.parent.absolute():
        raise ValueError('Local map directory must not be a symlink.')
    lock = destination.with_name(destination.name + '.lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'rb') as handle:
        check_private(os.fstat(handle.fileno()))
        fcntl.flock(handle, fcntl.LOCK_EX)
        if destination.is_symlink():
            raise ValueError('Local map destination must not be a symlink.')
        if destination.exists():
            check_private(destination.stat())
        yield


def check_private(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise ValueError('Expected a private file owned by this user.')


def save_map(destination, mapping):
    from library.snapshots import MAX_BYTES, MAX_TRACKS, SnapshotError

    data = json.dumps(mapping, ensure_ascii=True).encode()
    if len(data) > MAX_BYTES or len(mapping['tracks']) > MAX_TRACKS:
        raise SnapshotError('Local map exceeds size limit.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)
