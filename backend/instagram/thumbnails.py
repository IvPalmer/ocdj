"""Bounded JPEGs in the existing private volume, independent of video expiry."""
import fcntl
import io
import re
import uuid
import warnings
from contextlib import contextmanager
from datetime import timedelta

from PIL import Image, ImageOps
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from . import cache

MAX_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 4_000_000
TTL = timedelta(days=30)
KEY = re.compile(r'[0-9a-f]{32}')


def root():
    directory = cache.root() / 'thumbnails'
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def path(key):
    if not KEY.fullmatch(key):
        raise ValidationError('Invalid thumbnail key')
    return root() / key


@contextmanager
def locked():
    with (root() / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def jpeg(raw):
    """Decode and re-encode: reject spoofed files and strip EXIF/private metadata."""
    if not 12 <= len(raw) <= MAX_BYTES:
        raise ValidationError('JPEG must be at most 128 KiB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'JPEG' or image.width * image.height > MAX_PIXELS:
                    raise ValueError
                image = ImageOps.exif_transpose(image)
                image.thumbnail((320, 320))
                out = io.BytesIO()
                image.convert('RGB').save(out, format='JPEG', quality=75)
                result = out.getvalue()
                if len(result) > MAX_BYTES:
                    raise ValueError
                return result
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError('Invalid JPEG thumbnail') from None


def store(raw):
    """Caller holds locked() through DB installation to fence cleanup/writers."""
    body = jpeg(raw)
    used = sum(p.stat().st_size for p in root().iterdir() if KEY.fullmatch(p.name))
    if used + len(body) > MAX_TOTAL_BYTES:
        raise ValidationError('Thumbnail cache full')
    key = uuid.uuid4().hex
    try:
        with path(key).open('xb') as out:
            path(key).chmod(0o600)
            out.write(body)
    except Exception:
        path(key).unlink(missing_ok=True)
        raise
    return key


def available(post, now=None):
    return bool(post.active and post.thumbnail_key and post.thumbnail_until
                and post.thumbnail_until > (now or timezone.now())
                and path(post.thumbnail_key).is_file())


def cleanup():
    from .models import Post
    with locked():
        now = timezone.now()
        Post.objects.filter(thumbnail_until__lte=now).update(thumbnail_key='', thumbnail_until=None)
        Post.objects.filter(active=False).update(thumbnail_key='', thumbnail_until=None)
        keep = set(Post.objects.exclude(thumbnail_key='').values_list('thumbnail_key', flat=True))
        for p in root().iterdir():
            if KEY.fullmatch(p.name) and p.name not in keep and now.timestamp() - p.stat().st_mtime > 300:
                p.unlink(missing_ok=True)
