"""TraxDB local-download API — the endpoints the Mac daemon calls.

The VPS scrapes the blog and tracks status; the Mac does the actual fetching
from Pixeldrain into its own archive. No audio is ever written to the VPS.

Cycle, per daemon poll:
    POST inventory/   -> Mac tells the VPS which date folders it already holds
    POST claim/       -> Mac leases up to `limit` pending lists
    POST <id>/complete/ or <id>/fail/  -> Mac reports the outcome, presenting
                                          the claim token it was handed

The rule the whole design serves: a date folder is atomic. The operator prunes
individual tracks out of folders by hand, so a folder that exists on the Mac —
even an empty one — must never be written into again.

Auth reuses the drain app's bearer token (DRAIN_TOKEN): same daemon host, same
trust boundary, one secret to rotate.
"""
import logging
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status as http_status

from drain.auth import require_drain_token

from .models import MacInventory, ScrapedFolder

logger = logging.getLogger(__name__)

MAX_BATCH = 25
# Generous: a 25-track FLAC list over a home connection can take a while, and a
# lease that expires mid-download hands the same work to a second worker.
LEASE_MINUTES = 180


def _claimable_q():
    """Pending, or claimed so long ago the claimer is presumed dead."""
    cutoff = timezone.now() - timedelta(minutes=LEASE_MINUTES)
    return (
        Q(download_status='pending')
        | Q(download_status='downloading', claimed_at__isnull=True)
        | Q(download_status='downloading', claimed_at__lt=cutoff)
    )


def _check_token(folder, request):
    """Reject an outcome reported against a lease the caller no longer holds.

    Returns an error Response, or None when the caller may proceed.
    """
    presented = str(request.data.get('claim_token') or '')
    if not folder.claim_token:
        # Nothing outstanding — an unclaimed folder has no outcome to report.
        return Response(
            {'error': 'folder is not claimed'},
            status=http_status.HTTP_409_CONFLICT,
        )
    if not secrets.compare_digest(presented, folder.claim_token):
        logger.warning(
            'traxdb local: stale claim token presented for %s', folder.folder_id,
        )
        return Response(
            {'error': 'stale claim token — this lease was reassigned'},
            status=http_status.HTTP_409_CONFLICT,
        )
    return None


@api_view(['POST'])
@require_drain_token
def local_inventory(request):
    """Mac reports the date folders it holds.

    Body: {"date_dirs": ["2026-07-15", ...]} — every YYYY-MM-DD folder that
    exists, empty ones included. An emptied folder is the operator saying they
    want nothing from that date; reporting only non-empty folders would let the
    daemon refill it.
    """
    date_dirs = request.data.get('date_dirs')
    if not isinstance(date_dirs, list):
        return Response(
            {'error': 'date_dirs must be a list of YYYY-MM-DD strings'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    def _int(key):
        try:
            v = request.data.get(key)
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # Optional, and only the daemon can know them: its poll interval lives in a
    # launchd plist and its batch size in a Mac env var. Reported so the panel
    # can quote a real cadence instead of a server-side guess.
    row = MacInventory.report(
        date_dirs, file_count=_int('file_count'), total_bytes=_int('total_bytes'),
        poll_interval_seconds=_int('poll_interval_seconds'),
        batch_limit=_int('batch_limit'),
    )
    logger.info('traxdb local: Mac reported %d date dirs', len(row.date_dirs))
    return Response({'count': len(row.date_dirs), 'reported_at': row.reported_at})


@api_view(['POST'])
@require_drain_token
def local_claim(request):
    """Lease up to `limit` pending lists for the Mac to download.

    POST, not GET: it mutates (leases) the rows it returns.

    Claimed folders flip to 'downloading' with a token the daemon must present
    to report an outcome. A claim older than LEASE_MINUTES is treated as
    abandoned and re-offered with a fresh token, which invalidates the original
    holder — a daemon that died mid-download must not strand the list.
    """
    try:
        limit = int(request.data.get('limit', 1))
    except (TypeError, ValueError):
        limit = 1
    limit = max(1, min(limit, MAX_BATCH))

    # Never hand out a date the Mac already holds: the folder may have been
    # queued before that date existed locally, and downloading it would merge
    # files into a collection the operator pruned by hand.
    held = set(MacInventory.current())

    items = []
    with transaction.atomic():
        candidates = (
            ScrapedFolder.objects
            .select_for_update(skip_locked=True)
            .filter(_claimable_q())
            .exclude(inferred_date__in=held)
            .order_by('-inferred_date', '-id')[:limit * 2]
        )
        now = timezone.now()
        batch_dates = set()
        for f in candidates:
            if len(items) >= limit:
                break
            # Two lists on one date share a destination folder; handing both out
            # would make one of them a merge. Only ever lease the first.
            if f.inferred_date and f.inferred_date in batch_dates:
                continue
            batch_dates.add(f.inferred_date)

            f.download_status = 'downloading'
            f.claimed_at = now
            f.claim_token = secrets.token_urlsafe(24)
            f.save(update_fields=['download_status', 'claimed_at', 'claim_token'])
            items.append({
                'id': f.id,
                'folder_id': f.folder_id,
                'claim_token': f.claim_token,
                'inferred_date': f.inferred_date,
                'pixeldrain_url': f.pixeldrain_url,
                'source_url': f.url,
                'tracks': [
                    {
                        'id': t.id,
                        'filename': t.filename,
                        'pixeldrain_file_id': t.pixeldrain_file_id,
                        'size': t.file_size_bytes,
                    }
                    for t in f.tracks.all() if t.pixeldrain_file_id
                ],
            })

    return Response({'items': items, 'count': len(items)})


@api_view(['POST'])
@require_drain_token
def local_complete(request, pk):
    """Mac reports a list as fully downloaded.

    Body: {"claim_token": "...", "files": [{"pixeldrain_file_id": "...",
           "local_path": "...", "bytes": 123}]}

    Every expected track must be accounted for. A partial report means a
    partial folder, and marking that 'downloaded' would hide the gap forever —
    the date then lands in the inventory and is never offered again.
    """
    try:
        folder = ScrapedFolder.objects.get(pk=pk)
    except ScrapedFolder.DoesNotExist:
        return Response({'error': 'not found'}, status=http_status.HTTP_404_NOT_FOUND)

    # Idempotent: a daemon that crashed after writing files but before its POST
    # landed retries, and must not be told it lost the lease.
    if folder.download_status == 'downloaded':
        return Response({
            'folder_id': folder.folder_id,
            'download_status': 'downloaded',
            'tracks_downloaded': folder.tracks.filter(downloaded=True).count(),
            'idempotent': True,
        })

    err = _check_token(folder, request)
    if err:
        return err

    files = request.data.get('files') or []
    by_file_id = {
        str(f.get('pixeldrain_file_id')): f
        for f in files if isinstance(f, dict) and f.get('pixeldrain_file_id')
    }

    expected = {t.pixeldrain_file_id for t in folder.tracks.all() if t.pixeldrain_file_id}
    if not expected:
        return Response(
            {'error': 'folder has no downloadable tracks recorded'},
            status=http_status.HTTP_409_CONFLICT,
        )
    missing = expected - set(by_file_id)
    if missing:
        return Response(
            {'error': f'incomplete: {len(missing)} of {len(expected)} tracks not reported',
             'missing': sorted(missing)[:10]},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        for track in folder.tracks.all():
            rec = by_file_id.get(track.pixeldrain_file_id)
            if not rec:
                continue
            track.downloaded = True
            track.download_status = 'downloaded'
            track.local_path = str(rec.get('local_path') or '')
            track.save(update_fields=['downloaded', 'download_status', 'local_path'])

        folder.download_status = 'downloaded'
        folder.claimed_at = None
        folder.claim_token = ''
        folder.last_error = ''
        folder.last_error_at = None
        folder.save(update_fields=[
            'download_status', 'claimed_at', 'claim_token', 'last_error', 'last_error_at',
        ])

        # The folder exists on the Mac now — record it immediately rather than
        # waiting for the next poll, so a sync in between can't re-queue it.
        # merge=True so this can't race a concurrent full report into dropping
        # dates the daemon reported a moment ago.
        if folder.inferred_date:
            MacInventory.report([folder.inferred_date], merge=True)

    logger.info(
        'traxdb local: folder %s completed on Mac (%d tracks)',
        folder.folder_id, len(expected),
    )
    return Response({
        'folder_id': folder.folder_id,
        'download_status': 'downloaded',
        'tracks_downloaded': len(expected),
    })


@api_view(['POST'])
@require_drain_token
def local_fail(request, pk):
    """Mac reports a list it could not download.

    Body: {"claim_token": "...", "reason": "..."}

    Parks in 'failed' rather than bouncing back to 'pending': the common cause
    is a dead Pixeldrain list (404), which would otherwise retry forever.
    """
    try:
        folder = ScrapedFolder.objects.get(pk=pk)
    except ScrapedFolder.DoesNotExist:
        return Response({'error': 'not found'}, status=http_status.HTTP_404_NOT_FOUND)

    if folder.download_status == 'downloaded':
        # A stale worker must never undo a completed download.
        return Response(
            {'error': 'folder already downloaded'},
            status=http_status.HTTP_409_CONFLICT,
        )

    err = _check_token(folder, request)
    if err:
        return err

    reason = str(request.data.get('reason') or 'unspecified')[:500]
    folder.download_status = 'failed'
    folder.claimed_at = None
    folder.claim_token = ''
    # Keep the reason. It used to live only in this log line, so the panel could
    # say "1 list failed" but never "…because the Pixeldrain key is expired" —
    # the one sentence that turns a mystery into a fix.
    folder.last_error = reason
    folder.last_error_at = timezone.now()
    folder.save(update_fields=[
        'download_status', 'claimed_at', 'claim_token', 'last_error', 'last_error_at',
    ])
    logger.error('traxdb local: folder %s failed on Mac: %s', folder.folder_id, reason)
    return Response({'folder_id': folder.folder_id, 'download_status': 'failed'})
