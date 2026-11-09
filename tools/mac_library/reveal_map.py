"""Private Mac-only sidecar. Never part of the server snapshot payload."""
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
    from library.snapshots import EXCLUDED, FORMATS, MAX_BYTES, SnapshotError, read_audio, validate

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
    data = json.dumps(dict(version=1, collection_id=payload['collection_id'],
                           root=str(root), tracks=paths, review_fingerprints=fingerprints), ensure_ascii=True).encode()
    if len(data) > MAX_BYTES:
        raise SnapshotError('Local map exceeds size limit.')
    destination = Path(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.parent.resolve(strict=True) != destination.parent.absolute():
        raise SnapshotError('Local map directory must not be a symlink.')
    if destination.is_symlink():
        raise SnapshotError('Local map destination must not be a symlink.')
    if destination.exists():
        info = destination.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SnapshotError('Existing local map must be private and owned by this user.')
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
