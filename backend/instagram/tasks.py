"""Retention uses the existing Huey worker; no new Mac service or login."""
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task
from . import cache
from .cache import cleanup
from .cleanup import capture_accepted_identification


@db_periodic_task(crontab(minute='*/30'))
def cleanup_instagram_cache():
    cleanup()


@db_task()
def recognize_cached(pk):
    """Run existing bounded ffmpeg/Shazam path on an already private cached file.

    No browser credentials or upstream media URLs are needed on the VPS. Fence
    results with the same lease used by the connector and manual corrections.
    """
    import asyncio
    import secrets
    import shutil
    import tempfile
    from datetime import timedelta
    from pathlib import Path
    from django.db import transaction
    from django.utils import timezone
    from .models import Media
    from .recognition import recognize

    with transaction.atomic():
        m = Media.objects.select_for_update().get(pk=pk)
        now = timezone.now()
        if (not m.queued or not m.active or not m.post.active
                or (m.lease_until and m.lease_until > now)):
            return
        if not m.cache_key or not m.cache_until or m.cache_until <= now:
            return  # selected source still needs the authorized Mac transport
        token = secrets.token_hex(24)
        key = m.cache_key
        m.lease_action = 'recognize'
        m.lease_token = token; m.lease_until = now + timedelta(minutes=10)
        m.status = 'running'; m.save()
    try:
        with tempfile.TemporaryDirectory(prefix='instagram-recognition-') as directory:
            video = Path(directory) / 'source.mp4'
            # Copy before external calls; expiry/replacement can remove the cache.
            shutil.copyfile(cache.path(key), video)
            candidates = asyncio.run(recognize(video, directory, 'ffmpeg'))
            status = 'identified' if candidates else 'no-match'
    except Exception:
        # Never log source paths, audio metadata, Shazam bodies or URLs.
        status, candidates = 'error', []
    with transaction.atomic():
        m = Media.objects.select_for_update().get(pk=pk)
        if (m.lease_token != token or not m.queued or not m.active or not m.post.active
                or not m.lease_until or m.lease_until <= timezone.now()):
            return
        m.status = status; m.candidates = candidates; m.queued = False
        m.recognition = dict(source='shazam', certainty='tentative', outcome=status, comments='not-read')
        m.lease_token = ''; m.lease_until = None; m.lease_action = ''
        accepted = len(candidates) == 1 and not m.reviewed and not (m.artist or m.title)
        if accepted:
            m.artist = candidates[0]['artist']; m.title = candidates[0]['title']
        m.save()
        if accepted:
            capture_accepted_identification(m, route='cached', accepted=True)
