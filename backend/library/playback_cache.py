"""Private MP3 staging. Serialized writes, hard quota, expiry, no static route."""
import fcntl
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from mutagen.mp3 import MP3

MAX_BYTES = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
TTL_SECONDS = 1800
KEY = re.compile(r'[0-9a-f]{32}')


def root():
    directory = Path(getattr(settings, 'MAC_PLAYBACK_CACHE_ROOT', settings.BASE_DIR / '.cache' / 'mac-playback'))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def path(key):
    if not KEY.fullmatch(key):
        raise ValueError('Invalid cache key')
    return root() / key


def cleanup():
    for item in root().iterdir():
        if KEY.fullmatch(item.name) and time.time() - item.stat().st_mtime >= TTL_SECONDS:
            item.unlink(missing_ok=True)


@contextmanager
def staged(stream):
    """Hold quota lock through DB commit; failures and stale leases leave no file."""
    destination = path(uuid.uuid4().hex)
    with (root() / '.upload-lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cleanup()
        used = sum(p.stat().st_size for p in root().iterdir() if KEY.fullmatch(p.name))
        try:
            with destination.open('xb') as output:
                os.chmod(destination, 0o600)
                size = 0
                while chunk := stream.read(65536):
                    size += len(chunk)
                    if size > MAX_BYTES or used + size > MAX_TOTAL:
                        raise ValueError('Playback cache limit reached')
                    output.write(chunk)
            if size < 12:
                raise ValueError('Invalid MP3')
            # Validate media, never trust upload extension or content type.
            MP3(destination)
            yield destination.name
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def byte_range(value, size):
    if not value:
        return 0, size - 1, 200
    match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', value)
    if not match or not any(match.groups()):
        raise ValueError('Invalid range')
    a, b = match.groups()
    start = int(a) if a else max(0, size - int(b))
    end = min(int(b), size - 1) if a and b else size - 1
    if start > end or start >= size:
        raise ValueError('Unsatisfiable range')
    return start, end, 206
