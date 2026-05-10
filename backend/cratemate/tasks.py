"""Cratemate Huey tasks — for slow paths that exceed HTTP request timeouts.

V1 keeps the /identify endpoint synchronous (asyncio.run inside the view)
because the typical Gemini call returns in 2-4 seconds. This module is here
for V2 work — e.g. batch identify of crate photos, or background enrichment
of partial results.
"""
from huey.contrib.djhuey import db_task


@db_task(retries=0, retry_delay=60)
def enrich_release_async(release_id: int):
    """Placeholder — V2 will refresh Discogs/Spotify/Bandcamp data for an
    IdentifiedRelease without blocking the original /identify response."""
    from .models import IdentifiedRelease
    try:
        IdentifiedRelease.objects.get(pk=release_id)
    except IdentifiedRelease.DoesNotExist:
        return {'error': 'not found'}
    # TODO V2: real enrichment.
    return {'release_id': release_id, 'note': 'V2 stub'}
