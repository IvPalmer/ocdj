"""Proposal-only receipts for accepted Instagram metadata, never raw candidates."""
import logging
import uuid

from django.db import transaction

logger = logging.getLogger(__name__)


def capture_accepted_identification(media, *, route, accepted, previous=None):
    """Caller owns the Media row lock and accepted-write transaction.

    Keep the UUID on the accepted result before invoking the optional adapter.
    A caption replay can retry that same event; fenced audio completions are new
    events. Callback DB failures roll back their savepoint, not review/playback.
    Successful receipt and metadata still commit/roll back together.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('Accepted identification requires a transaction')
    if (media.status != 'identified' or media.reviewed or len(media.candidates) != 1
            or media.candidates[0] != {'artist': media.artist, 'title': media.title}
            or not media.artist or not media.title):
        return
    # Never copy connector records, private captions/URLs, tokens or raw bodies.
    evidence = dict(source=media.recognition.get('source'), route=route,
                    certainty=media.recognition.get('certainty'))
    if evidence['source'] not in ('caption', 'comment', 'shazam'):
        return
    if evidence['certainty'] not in ('explicit', 'tentative'):
        return
    event = None
    if route == 'caption' and previous:
        artist, title, recognition = previous
        if ((artist, title) == (media.artist, media.title)
                and recognition.get('source') == evidence['source']
                and recognition.get('certainty') == evidence['certainty']):
            event = recognition.get('cleanup_event')
    if not accepted and not event:
        return  # pre-existing human metadata is not a new accepted identification
    event = event or str(uuid.uuid4())
    media.recognition['cleanup_event'] = event
    media.save(update_fields=['recognition'])
    try:
        # Import and execute inside the savepoint: even a partial callback write
        # or DatabaseError cannot poison the owning acceptance transaction.
        with transaction.atomic():
            from wanted.services.cleanup import capture_external_identification
            capture_external_identification(event_id=event, artist=media.artist,
                                            title=media.title, evidence=evidence)
    except Exception:
        # No exception text: it may contain private metadata or provider payloads.
        logger.warning('Accepted identification cleanup capture unavailable')
