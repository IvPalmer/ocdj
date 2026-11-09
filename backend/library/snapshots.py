"""Path-free metadata snapshots. Pure Python: never initializes Django or a DB."""
import hashlib
import hmac
import json
import math
import os
import re
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import mutagen
from mutagen.id3 import ID3
from mutagen.mp3 import HeaderNotFoundError

FORMATS = {'.mp3': 'mp3', '.flac': 'flac', '.aif': 'aiff', '.aiff': 'aiff',
           '.wav': 'wav', '.ogg': 'ogg', '.m4a': 'm4a'}
EXCLUDED = {'id3', 'traxdb'}
MAX_BYTES = 32 * 1024 * 1024
MAX_TRACKS = 100000
SOURCE = 'mac_snapshot'
TIME_FORMAT = '%Y-%m-%dT%H:%M:%S.%fZ'
ROW_FIELDS = {'id', 'artist', 'title', 'format', 'bitrate', 'sample_rate',
              'duration_seconds', 'file_size_bytes'}


class SnapshotError(ValueError):
    """Messages contain no input values, names, filesystem paths or exceptions."""


def read_metadata(handle, fmt):
    """Mutagen reads only; ID3 fallback covers AIFF/WAV's non-easy wrappers."""
    raw = mutagen.File(handle)
    if raw is None or raw.info is None:
        raise SnapshotError('Unsupported or unreadable audio.')
    handle.seek(0)
    easy = mutagen.File(handle, easy=True)
    tags = easy.tags if easy is not None and easy.tags is not None else {}
    raw_tags = raw.tags if raw.tags is not None else {}

    def tag(name, frame):
        value = tags.get(name)
        if value:
            return str(value[0]).strip()
        value = raw_tags.get(frame)
        return str(value.text[0]).strip() if value is not None and hasattr(value, 'text') and value.text else ''

    def positive(value):
        return int(value) if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else None

    bitrate = positive(getattr(raw.info, 'bitrate', None))
    duration = getattr(raw.info, 'length', None)
    return dict(artist=tag('artist', 'TPE1'), title=tag('title', 'TIT2'), format=fmt,
                bitrate=positive(bitrate // 1000) if bitrate else None,
                sample_rate=positive(getattr(raw.info, 'sample_rate', None)),
                duration_seconds=duration if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0 else None)


def read_audio(handle, fmt, size):
    """Skip only provably non-audio placeholders."""
    if size == 0:
        return None
    try:
        return read_metadata(handle, fmt)
    except HeaderNotFoundError:
        if fmt != 'mp3':
            raise
        handle.seek(0)
        tag_size = ID3(handle).size
        # An MPEG frame header alone needs four bytes after the ID3 tag.
        if not (tag_size > 0 and 0 <= size - tag_size < 4):
            raise
        return None


def scan(root, collection_id, key):
    """Explicit root; ID3/TraxDB and symlinks are outside this snapshot's scope.

    Errors make the snapshot non-exportable. No filename-derived identity tags.
    Stable IDs are HMACs of collection + relative path (renames are new IDs).
    """
    root = Path(root).expanduser()
    if (not root.is_dir() or root.is_symlink() or
            any(part.casefold() in EXCLUDED for part in root.resolve().parts)):
        raise SnapshotError('Choose a readable library root outside staging/archive.')
    if str(uuid.UUID(collection_id)) != collection_id or len(key) != 32:
        raise SnapshotError('Invalid collection identity or key.')
    counts = dict(audio_candidates=0, readable=0, tagged_identity=0, measured_quality=0,
                  read_errors=0, walk_errors=0, excluded_staging_dirs=0, skipped_symlinks=0,
                  excluded_non_audio=0)
    formats = {}
    failures = {}
    rows = []
    started = datetime.now(timezone.utc).strftime(TIME_FORMAT)

    def walk_error(_error):
        counts['walk_errors'] += 1

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in list(dirs):
            path = Path(directory) / name
            if name.casefold() in EXCLUDED:
                counts['excluded_staging_dirs'] += 1
                dirs.remove(name)
            elif path.is_symlink():
                counts['skipped_symlinks'] += 1
                dirs.remove(name)
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                counts['skipped_symlinks'] += 1
                continue
            fmt = FORMATS.get(path.suffix.lower())
            if not fmt:
                continue
            counts['audio_candidates'] += 1
            phase = 'file_access'
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as handle:
                    before = os.fstat(handle.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise SnapshotError('Not a regular audio file.')
                    phase = 'metadata_read'
                    row = read_audio(handle, fmt, before.st_size)
                    after = os.fstat(handle.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    phase = 'file_changed'
                    raise SnapshotError('File changed during metadata read.')
                if row is None:
                    counts['excluded_non_audio'] += 1
                    continue
                relative = os.fsencode(path.relative_to(root))
                row['id'] = hmac.new(key, collection_id.encode() + b'\0' + relative, hashlib.sha256).hexdigest()
                row['file_size_bytes'] = before.st_size
                phase = 'metadata_validation'
                validate_row(row)
                rows.append(row)
                counts['readable'] += 1
                counts['tagged_identity'] += bool(row['artist'] and row['title'])
                counts['measured_quality'] += bool(row['bitrate'] and row['sample_rate'])
                formats[fmt] = formats.get(fmt, 0) + 1
            except Exception:
                # Mutagen/OSError text may contain private filenames. Never log it.
                counts['read_errors'] += 1
                failures[phase] = failures.get(phase, 0) + 1
    counts['failures_by_stage'] = failures
    counts['by_format'] = formats
    complete = not (counts['read_errors'] or counts['walk_errors'])
    payload = dict(version=1, collection_id=collection_id, captured_at=started,
                   complete=complete, tracks=sorted(rows, key=lambda row: row['id']))
    return payload, counts


def validate_row(row):
    if not isinstance(row, dict) or set(row) != ROW_FIELDS:
        raise SnapshotError('Invalid track fields.')
    if not isinstance(row['id'], str) or not re.fullmatch('[0-9a-f]{64}', row['id']):
        raise SnapshotError('Invalid opaque track ID.')
    for field in ('artist', 'title'):
        value = row[field]
        if not isinstance(value, str) or len(value) > 500 or any(ord(c) < 32 or 0xd800 <= ord(c) <= 0xdfff for c in value):
            raise SnapshotError('Invalid identity tag.')
    if row['format'] not in FORMATS.values():
        raise SnapshotError('Invalid format.')
    for field, maximum in [('bitrate', 100000), ('sample_rate', 10000000), ('file_size_bytes', 2**63 - 1)]:
        value = row[field]
        if value is None and field != 'file_size_bytes':
            continue
        minimum = 0 if field == 'file_size_bytes' else 1
        if type(value) is not int or not minimum <= value <= maximum:
            raise SnapshotError('Invalid measured quality or size.')
    value = row['duration_seconds']
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 31536000):
        raise SnapshotError('Invalid duration.')


def validate(payload):
    try:
        if not isinstance(payload, dict) or set(payload) != {'version', 'collection_id', 'captured_at', 'complete', 'tracks'}:
            raise SnapshotError('Invalid snapshot fields.')
        if type(payload['version']) is not int or payload['version'] != 1 or payload['complete'] is not True:
            raise SnapshotError('Only complete version 1 snapshots can be imported.')
        if str(uuid.UUID(payload['collection_id'])) != payload['collection_id']:
            raise SnapshotError('Invalid collection ID.')
        stamp = datetime.strptime(payload['captured_at'], TIME_FORMAT).replace(tzinfo=timezone.utc)
        if stamp.strftime(TIME_FORMAT) != payload['captured_at'] or stamp > datetime.now(timezone.utc):
            raise SnapshotError('Invalid capture timestamp.')
        if not isinstance(payload['tracks'], list) or not 1 <= len(payload['tracks']) <= MAX_TRACKS:
            raise SnapshotError('Snapshot must contain 1–100000 tracks; empty replacement is refused.')
        seen = set()
        for row in payload['tracks']:
            validate_row(row)
            if row['id'] in seen:
                raise SnapshotError('Duplicate track ID.')
            seen.add(row['id'])
    except (TypeError, ValueError, AttributeError, KeyError):
        raise SnapshotError('Invalid or incomplete metadata snapshot.') from None
    return payload


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def replay_status(payload, previous_capture, previous_digest):
    if previous_capture is None:
        return 'new'
    if payload['captured_at'] == previous_capture and digest(payload) == previous_digest:
        return 'replay'
    if payload['captured_at'] <= previous_capture:
        raise SnapshotError('Stale or conflicting snapshot; no changes applied.')
    return 'new'


def load_snapshot(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SnapshotError('Duplicate JSON field.')
            result[key] = value
        return result
    try:
        with open(path, 'rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SnapshotError('Snapshot exceeds size limit.')
        return validate(json.loads(raw, object_pairs_hook=unique_pairs))
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise SnapshotError('Cannot read a valid bounded snapshot.') from None


def write_snapshot(path, payload):
    """Publish a private complete file atomically, never replace an existing file."""
    validate(payload)
    data = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()
    if len(data) > MAX_BYTES:
        raise SnapshotError('Snapshot exceeds size limit.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=Path(path).parent, delete=False) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except OSError:
        raise SnapshotError('Export failed; destination must be new and privately writable.') from None
    finally:
        if temporary is not None:
            os.unlink(temporary)
