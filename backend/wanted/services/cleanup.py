"""Durable outbox, fenced retries and atomic cleanup/audit application."""
import logging
import uuid
from copy import deepcopy
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from wanted.models import MetadataCleanup, WantedItem
from .cleanup_evidence import clean_isrc, collect_evidence, evaluate, same_name

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE = timedelta(minutes=5)


def dispatch(cleanup_id):
    try:
        from wanted.tasks import task_metadata_cleanup
        task_metadata_cleanup(cleanup_id)
    except Exception:
        # The committed DB row is the outbox; the periodic sweep recovers it.
        # Never log exceptions containing provider queries or source metadata.
        logger.warning('Metadata cleanup dispatch deferred to outbox sweep')


def capture_wanted(item):
    snapshot = {'artist': item.artist, 'title': item.title, 'isrc': clean_isrc(item.cleanup_isrc),
                'protected': item.metadata_protected, 'revision': item.metadata_revision}
    return _capture(f'wanted:{item.pk}', snapshot, wanted_id=item.pk)


def _capture(key, snapshot, **subject):
    receipt, created = MetadataCleanup.objects.get_or_create(
        subject_key=key, defaults={'snapshot': deepcopy(snapshot), **subject})
    if created:
        transaction.on_commit(lambda: dispatch(receipt.pk))
    return receipt


def capture_external_identification(*, event_id, artist, title, evidence):
    """Parent's Instagram adapter: stable event UUID; durable proposal only.

    Call inside the accepted-write transaction. Do not pass private raw payloads.
    Replays reuse event_id; a later accepted identification uses a new UUID.
    No Instagram metadata is ever written by this module.
    """
    key = f'external:{uuid.UUID(str(event_id))}'
    snapshot = {'artist': artist, 'title': title, 'isrc': '', 'protected': True,
                'source_evidence': deepcopy(evidence)}
    with transaction.atomic():
        receipt = _capture(key, snapshot)
        if receipt.snapshot != snapshot:
            raise ValueError('Cleanup event reused for different evidence')
        return receipt


def _anchor(track, raw_results):
    # A cluster can combine different versions; every overlapping raw hit must
    # retain the same full title and artist before carrying its ISRC forward.
    hits = [r['track'] for r in raw_results if r.get('track')
            and track.get('timestamp_start', -1) <= r.get('start_sec', -2)
            < track.get('timestamp_end', -1)]
    if not hits or any(not same_name(h.get('title'), track.get('title'))
                       or not same_name(h.get('artist', ''), track.get('artist', '')) for h in hits):
        return ''
    codes = {clean_isrc(h.get('isrc')) for h in hits}
    return next(iter(codes)) if len(codes) == 1 and '' not in codes else ''


def _overlap(a, b):
    return not (a.get('timestamp_end', 0) <= b.get('timestamp_start', 0)
                or a.get('timestamp_start', 0) >= b.get('timestamp_end', 0))


def publish_recognition(job, tracklist):
    """Save accepted tracks and their intents atomically, preserving current edits.

    Stable occurrence UUIDs live in track rows, never array indexes or a claimed
    canonical recording ID. Match existing rows only on full metadata + interval.
    """
    from recognize.models import RecognizeJob
    with transaction.atomic():
        current = RecognizeJob.objects.select_for_update().get(pk=job.pk)
        manual = [deepcopy(t) for t in current.tracklist if t.get('manual')]
        tracks = [deepcopy(t) for t in tracklist if not t.get('manual')
                  and not any(_overlap(t, m) for m in manual)] + manual
        tracks.sort(key=lambda t: t.get('timestamp_start', 0))
        identity = lambda t: tuple(t.get(k) for k in ('artist', 'title', 'timestamp_start', 'timestamp_end'))
        previous = {identity(t): t for t in current.tracklist}
        # A repeat cluster pass starts from raw tags. Retain a previously
        # applied cleanup on the same occurrence rather than undo/reapply it.
        receipts = {r.subject_key: r for r in MetadataCleanup.objects.filter(job_id=job.pk, status='applied')}
        for old in current.tracklist:
            receipt = receipts.get(f"recognize:{job.pk}:{old.get('cleanup_id', '')}")
            if receipt:
                previous[identity(receipt.snapshot['track'])] = old
        for track in tracks:
            old = previous.get(identity(track), {})
            # Do not accept externally supplied cleanup IDs.
            occurrence = old.get('cleanup_id') or str(uuid.uuid4())
            track['cleanup_id'] = occurrence
            if f'recognize:{job.pk}:{occurrence}' in receipts:
                track['artist'], track['title'] = old.get('artist', ''), old.get('title', '')
            snapshot = {'artist': track.get('artist', ''), 'title': track.get('title', ''),
                        'isrc': _anchor(track, job.raw_results), 'protected': bool(track.get('manual')),
                        'track': deepcopy(track)}
            _capture(f'recognize:{job.pk}:{occurrence}', snapshot, job_id=job.pk)
        job.tracklist = tracks
        job.tracks_found = len(tracks)
        # Avoid stale job object fields overwriting concurrent metadata edits.
        job.save(update_fields=['tracklist', 'tracks_found', 'status', 'engine', 'segments_done', 'updated'])
    return tracks


def due_cleanups():
    now = timezone.now()
    return MetadataCleanup.objects.filter(
        Q(status='queued', available_at__lte=now) | Q(status='researching', available_at__lte=now)
    ).order_by('available_at', 'pk')


def process_cleanup(cleanup_id):
    with transaction.atomic():
        receipt = MetadataCleanup.objects.select_for_update().get(pk=cleanup_id)
        now = timezone.now()
        if receipt.status not in ('queued', 'researching') or receipt.available_at > now:
            return
        if receipt.attempts >= MAX_ATTEMPTS:
            receipt.status = 'failed'
            receipt.result = {'changes': {}, 'reason': 'attempts_exhausted'}
            receipt.save(update_fields=['status', 'result', 'updated'])
            return
        token = uuid.uuid4()
        receipt.lease = token
        receipt.attempts += 1
        receipt.status = 'researching'
        receipt.available_at = now + LEASE
        receipt.save(update_fields=['lease', 'attempts', 'status', 'available_at', 'updated'])
        snapshot = deepcopy(receipt.snapshot)
    try:
        evidence = collect_evidence(snapshot)
        result = evaluate(snapshot, evidence)
    except Exception as exc:
        # Retain a safe classification only, never provider error text.
        import requests
        transient = isinstance(exc, requests.RequestException)
        with transaction.atomic():
            receipt = MetadataCleanup.objects.select_for_update().get(pk=cleanup_id)
            if receipt.lease != token or receipt.status != 'researching':
                return
            receipt.status = 'queued' if transient and receipt.attempts < MAX_ATTEMPTS else 'failed'
            receipt.available_at = timezone.now() + timedelta(minutes=2)
            receipt.result = {'changes': {}, 'reason': 'transport_failure' if transient else 'invalid_evidence'}
            receipt.save(update_fields=['status', 'available_at', 'result', 'updated'])
        return
    with transaction.atomic():
        receipt = MetadataCleanup.objects.select_for_update().get(pk=cleanup_id)
        if (receipt.lease != token or receipt.status != 'researching'
                or receipt.available_at <= timezone.now()):
            return
        receipt.evidence = evidence
        receipt.result = result
        receipt.status = 'protected' if snapshot.get('protected') else 'proposed'
        if receipt.wanted_id:
            current = WantedItem.objects.select_for_update().filter(pk=receipt.wanted_id).first()
            if (current is None or current.metadata_revision != snapshot['revision']
                    or current.metadata_protected or current.artist != snapshot['artist']
                    or current.title != snapshot['title'] or current.cleanup_isrc != snapshot['isrc']):
                receipt.status = 'superseded'
            elif result['changes']:
                # Compare-and-set in the same transaction as the immutable before/
                # evidence/after receipt. QuerySet avoids the manual-edit boundary.
                applied = WantedItem.objects.filter(
                    pk=current.pk, metadata_revision=snapshot['revision'], metadata_protected=False,
                    artist=snapshot['artist'], title=snapshot['title'], cleanup_isrc=snapshot['isrc'],
                ).update(**result['changes'], metadata_revision=current.metadata_revision + 1,
                         updated=timezone.now(), preview_url='', preview_provider='', preview_checked=None)
                receipt.status = 'applied' if applied else 'superseded'
        elif receipt.job_id:
            from recognize.models import RecognizeJob
            job = RecognizeJob.objects.select_for_update().filter(pk=receipt.job_id).first()
            index = next((i for i, t in enumerate(job.tracklist)
                          if t.get('cleanup_id') == snapshot['track']['cleanup_id']), None) if job else None
            if index is None or job.tracklist[index] != snapshot['track'] or job.tracklist[index].get('manual'):
                receipt.status = 'superseded'
            elif result['changes']:
                tracks = deepcopy(job.tracklist)
                tracks[index].update(result['changes'])
                applied = RecognizeJob.objects.filter(pk=job.pk, tracklist=job.tracklist).update(
                    tracklist=tracks, updated=timezone.now())
                receipt.status = 'applied' if applied else 'superseded'
        # Applied is the only status that claims writes. Proposed deltas on a
        # superseded receipt remain hypotheses, not an application history.
        receipt.save(update_fields=['evidence', 'result', 'status', 'updated'])
