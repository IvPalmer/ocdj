"""Owned private cache, deliberately outside MEDIA_ROOT and nginx static routes."""
import os
import fcntl
import re
import uuid
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

MAX_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
TTL = timedelta(hours=24)


def root():
    p = Path(getattr(settings, 'INSTAGRAM_CACHE_ROOT', settings.BASE_DIR / '.cache' / 'instagram'))
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def path(key):
    if not re.fullmatch(r'[0-9a-f]{32}', key):
        raise ValidationError('Invalid cache key')
    return root() / key


def cleanup():
    from .models import Media
    now = timezone.now()
    Media.objects.filter(cache_until__lte=now).update(cache_key='', cache_until=None)
    Media.objects.filter(post__active=False).update(cache_key='', cache_until=None)
    Media.objects.filter(active=False).update(cache_key='', cache_until=None)
    keep = set(Media.objects.exclude(cache_key='').values_list('cache_key', flat=True))
    for p in root().iterdir():
        if re.fullmatch(r'[0-9a-f]{32}', p.name) and p.name not in keep and now.timestamp() - p.stat().st_mtime > 300:
            p.unlink(missing_ok=True)


def store(upload):
    if upload.content_type != 'video/mp4' or upload.size > MAX_BYTES or upload.size < 12:
        raise ValidationError('Expected MP4 video, at most 64 MiB')
    key = uuid.uuid4().hex
    p = path(key)
    try:
        with (root() / '.upload-lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            used = sum(item.stat().st_size for item in root().iterdir() if re.fullmatch(r'[0-9a-f]{32}', item.name))
            if used + upload.size > MAX_TOTAL_BYTES:
                raise ValidationError('Instagram cache full; wait for expiry or cleanup')
            _write(upload, p)
    except Exception:
        p.unlink(missing_ok=True)
        raise
    return key


def _write(upload, p):
    with p.open('xb') as out:
        os.chmod(p, 0o600)
        size = 0
        for chunk in upload.chunks():
            if size == 0 and chunk[4:8] != b'ftyp':
                raise ValidationError('Invalid MP4 header')
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValidationError('Video exceeds 64 MiB')
            out.write(chunk)
