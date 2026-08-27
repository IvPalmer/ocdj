import json
import logging
import os
import re
import threading

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import TraxDBOperation, ScrapedFolder, ScrapedTrack
from .serializers import (
    TraxDBOperationSerializer, TraxDBOperationListSerializer,
    TriggerSyncSerializer,
    TriggerDownloadSerializer,
    TriggerAuditSerializer,
    ScrapedFolderSerializer,
    ScrapedFolderDetailSerializer,
    ScrapedTrackSerializer,
)
from .services import run_sync, run_download, run_audit
from .tasks import task_sync, task_download, task_audit

from core.services.config import get_config

logger = logging.getLogger(__name__)


# ── Local inventory ──────────────────────────────────────────

def _seen_list_ids(traxdb_root):
    """Pixeldrain list IDs already accounted for. Bookkeeping, not media — it
    stays on the VPS next to the DB it complements even in Mac mode."""
    try:
        with open(os.path.join(traxdb_root, '.pixeldrain_lists_seen.json'),
                  'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _queue_context():
    """Held dates + lease cutoff, the two facts `queue_state` needs.

    Shared by the inventory counts and the folder list so a row and the chip
    counting it can never disagree about what state it is in.
    """
    from .models import MacInventory
    from .views_local import LEASE_MINUTES

    return {
        'held_dates': set(MacInventory.current()),
        'lease_cutoff': timezone.now() - timedelta(minutes=LEASE_MINUTES),
    }


def _queue_state():
    """Queue-facing counts + who is working on it.

    The panel's central question is "is this list downloading or waiting?".
    The stored `download_status` can't answer it on its own: a leased list sits
    in 'downloading' whether the Mac is fetching it right now or merely holds
    it in a batch, and a pending list whose date already exists on the Mac will
    never be claimed at all. Both distinctions are computed here so the counts
    match `queue_state` on the rows exactly.

    None of this is derivable from an operation record — the daemon downloads
    on its own schedule without creating one.
    """
    from .models import MacInventory

    ctx = _queue_context()
    held, cutoff = ctx['held_dates'], ctx['lease_cutoff']

    counts = {'claimed': 0, 'stalled': 0, 'waiting': 0, 'blocked': 0,
              'failed': 0, 'downloaded': 0, 'skipped': 0}
    for f in ScrapedFolder.objects.only(
            'download_status', 'claimed_at', 'inferred_date').iterator():
        if f.download_status == 'downloading':
            key = 'claimed' if (f.claimed_at and f.claimed_at >= cutoff) else 'stalled'
        elif f.download_status == 'pending':
            key = 'blocked' if (f.inferred_date and f.inferred_date in held) else 'waiting'
        else:
            key = f.download_status
        if key in counts:
            counts[key] += 1

    row = MacInventory.objects.filter(pk=1).first()
    last_seen = row.reported_at if row else None
    interval = (row.poll_interval_seconds or 0) if row else 0

    # "Overdue", not "dead": the daemon reports at the top of each cycle, so
    # two missed intervals means we should have heard from it and didn't. That
    # signal was entirely absent when its Pixeldrain key expired and every
    # download 401'd behind a panel insisting all was well. Without a reported
    # interval there is no cadence to be overdue against, so don't claim one.
    overdue = False
    if last_seen and interval:
        overdue = (timezone.now() - last_seen).total_seconds() > interval * 2

    return {
        'queue': counts,
        # Newest list we know the blog has offered, from folders discovered by
        # past checks — not a live read of the blog, hence the name.
        'latest_known_list_date': (
            ScrapedFolder.objects.exclude(inferred_date='')
            .order_by('-inferred_date')
            .values_list('inferred_date', flat=True)
            .first()
        ),
        'daemon': {
            'last_seen': last_seen,
            'overdue': overdue,
            # None when the daemon has not reported its own cadence yet. The
            # panel must stay silent about timing rather than invent it.
            'interval_seconds': interval or None,
        },
    }


@api_view(['GET'])
def inventory(request):
    """Return TraxDB inventory stats (date dirs, file counts, known lists).

    In Mac mode the archive lives on the operator's machine and the VPS holds
    none of it, so the numbers come from what the daemon last reported. Scanning
    TRAXDB_ROOT here would truthfully report an empty directory and make the
    panel look like the whole archive had vanished.
    """
    traxdb_root = get_config('TRAXDB_ROOT')

    date_dirs = []
    file_count = 0
    total_bytes = 0

    if (get_config('TRAXDB_DOWNLOAD_TARGET') or 'mac').lower() != 'vps':
        from .models import MacInventory
        row = MacInventory.objects.filter(pk=1).first()
        date_dirs = sorted(row.date_dirs or []) if row else []
        return Response({
            'date_dirs_count': len(date_dirs),
            'latest_date': date_dirs[-1] if date_dirs else None,
            'oldest_date': date_dirs[0] if date_dirs else None,
            'known_lists_count': len(_seen_list_ids(traxdb_root)),
            'file_count': row.file_count if row else 0,
            'total_bytes': row.total_bytes if row else 0,
            'db_folders_total': ScrapedFolder.objects.count(),
            'db_folders_downloaded': ScrapedFolder.objects.filter(
                download_status='downloaded').count(),
            'archive_location': 'mac',
            'reported_at': row.reported_at if row else None,
            **_queue_state(),
        })

    try:
        for entry in os.scandir(traxdb_root):
            if entry.is_dir() and re.match(r'^\d{4}-\d{2}-\d{2}$', entry.name):
                date_dirs.append(entry.name)
                try:
                    for f in os.scandir(entry.path):
                        if f.is_file() and not f.name.startswith('.'):
                            file_count += 1
                            try:
                                total_bytes += f.stat().st_size
                            except OSError:
                                pass
                except OSError:
                    pass
    except OSError:
        pass

    seen_ids = _seen_list_ids(traxdb_root)

    date_dirs.sort()

    # Include DB folder counts
    db_folders_total = ScrapedFolder.objects.count()
    db_folders_downloaded = ScrapedFolder.objects.filter(download_status='downloaded').count()

    return Response({
        'date_dirs_count': len(date_dirs),
        'latest_date': date_dirs[-1] if date_dirs else None,
        'oldest_date': date_dirs[0] if date_dirs else None,
        'known_lists_count': len(seen_ids),
        'file_count': file_count,
        'total_bytes': total_bytes,
        'db_folders_total': db_folders_total,
        'db_folders_downloaded': db_folders_downloaded,
        'archive_location': 'vps',
        **_queue_state(),
    })


# ── Operations list ───────────────────────────────────────────

@api_view(['GET'])
def operations(request):
    """List all TraxDB operations, filterable by op_type and status."""
    qs = TraxDBOperation.objects.all()

    op_type = request.query_params.get('op_type')
    if op_type:
        qs = qs.filter(op_type=op_type)

    op_status = request.query_params.get('status')
    if op_status:
        qs = qs.filter(status=op_status)

    total = qs.count()
    limit = int(request.query_params.get('limit', 50))
    qs = qs[:limit]

    serializer = TraxDBOperationListSerializer(qs, many=True)
    return Response({'count': total, 'results': serializer.data})


@api_view(['GET'])
def operation_detail(request, pk):
    """Get a single operation with full summary."""
    try:
        op = TraxDBOperation.objects.get(pk=pk)
    except TraxDBOperation.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = TraxDBOperationSerializer(op)
    return Response(serializer.data)


# ── Trigger endpoints ─────────────────────────────────────────

# Per-op-type locks to atomically gate "is a run already in flight?". The
# previous check-then-create pattern lost the race when two POSTs arrived
# simultaneously — both passed `.exists()` and both spawned worker threads.
# The lock must be held through the .create() so the gap between check and
# insert can't be exploited by a concurrent caller.
_TRIGGER_LOCKS = {
    'sync': threading.Lock(),
    'download': threading.Lock(),
    'audit': threading.Lock(),
}


class _trigger_slot:
    """Context manager: acquires the per-op-type lock and validates no run is
    already in progress. Use `slot.claimed` to check; if False, return 409."""

    def __init__(self, op_type):
        self.op_type = op_type
        self.lock = _TRIGGER_LOCKS[op_type]
        self.claimed = False

    def __enter__(self):
        if not self.lock.acquire(blocking=False):
            return self
        if TraxDBOperation.objects.filter(op_type=self.op_type, status='running').exists():
            self.lock.release()
            return self
        self.claimed = True
        return self

    def __exit__(self, *exc):
        if self.claimed:
            self.lock.release()
        return False


def _get_latest_sync_report():
    """Return the report_path of the latest completed sync operation, or None."""
    op = TraxDBOperation.objects.filter(
        op_type='sync', status='completed'
    ).exclude(report_path='').first()
    if op and op.report_path and os.path.exists(op.report_path):
        return op.report_path
    return None


@api_view(['POST'])
def trigger_sync(request):
    """Trigger a blog sync operation."""
    ser = TriggerSyncSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    max_pages = ser.validated_data.get('max_pages', 50)

    with _trigger_slot('sync') as slot:
        if not slot.claimed:
            return Response(
                {'error': 'A sync is already running'},
                status=status.HTTP_409_CONFLICT,
            )
        op = TraxDBOperation.objects.create(op_type='sync', status='running')

    task_sync(op.id, max_pages=max_pages)

    return Response(
        TraxDBOperationSerializer(op).data,
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['POST'])
def trigger_download(request):
    """Trigger a download operation."""
    # Downloads belong on the Mac: the VPS is not a music host, and its disk is
    # far smaller than the archive. Refuse rather than quietly filling it again.
    if (get_config('TRAXDB_DOWNLOAD_TARGET') or 'mac').lower() != 'vps':
        return Response(
            {'error': 'TRAXDB_DOWNLOAD_TARGET=mac — the Mac daemon pulls pending lists '
                      'straight from Pixeldrain. Set it to "vps" only if you deliberately '
                      'want audio stored on the server.'},
            status=status.HTTP_409_CONFLICT,
        )

    ser = TriggerDownloadSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    links_key = ser.validated_data.get('links_key', 'links_new')

    # Determine which sync report to use
    sync_op_id = ser.validated_data.get('sync_operation_id')
    sync_report_path = None
    if sync_op_id:
        try:
            sync_op = TraxDBOperation.objects.get(id=sync_op_id, op_type='sync', status='completed')
            sync_report_path = sync_op.report_path if sync_op.report_path else None
        except TraxDBOperation.DoesNotExist:
            return Response(
                {'error': f'Sync operation {sync_op_id} not found or not completed'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        sync_report_path = _get_latest_sync_report()

    # Native mode: we can work without a report file if we have DB data
    latest_sync = TraxDBOperation.objects.filter(
        op_type='sync', status='completed'
    ).first()

    if not sync_report_path and not (latest_sync and latest_sync.summary.get('links_new')):
        # Check if we have pending folders in DB
        if not ScrapedFolder.objects.filter(download_status='pending').exists():
            return Response(
                {'error': 'No completed sync report or pending folders available. Run a sync first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    with _trigger_slot('download') as slot:
        if not slot.claimed:
            return Response(
                {'error': 'A download is already running'},
                status=status.HTTP_409_CONFLICT,
            )
        op = TraxDBOperation.objects.create(op_type='download', status='running')

    task_download(op.id, sync_report_path=sync_report_path, links_key=links_key)

    return Response(
        TraxDBOperationSerializer(op).data,
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['POST'])
def trigger_audit(request):
    """Trigger an audit operation."""
    ser = TriggerAuditSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    sync_op_id = ser.validated_data.get('sync_operation_id')
    sync_report_path = None
    if sync_op_id:
        try:
            sync_op = TraxDBOperation.objects.get(id=sync_op_id, op_type='sync', status='completed')
            sync_report_path = sync_op.report_path if sync_op.report_path else None
        except TraxDBOperation.DoesNotExist:
            return Response(
                {'error': f'Sync operation {sync_op_id} not found or not completed'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        sync_report_path = _get_latest_sync_report()

    # Native mode can work without a report if we have DB data
    if not sync_report_path and not ScrapedFolder.objects.filter(download_status='downloaded').exists():
        return Response(
            {'error': 'No completed sync report or downloaded folders available. Run a sync first.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with _trigger_slot('audit') as slot:
        if not slot.claimed:
            return Response(
                {'error': 'An audit is already running'},
                status=status.HTTP_409_CONFLICT,
            )
        op = TraxDBOperation.objects.create(op_type='audit', status='running')

    task_audit(op.id, sync_report_path=sync_report_path)

    return Response(
        TraxDBOperationSerializer(op).data,
        status=status.HTTP_202_ACCEPTED,
    )


# ── Download progress + cancel ────────────────────────────────

@api_view(['GET'])
def download_progress(request, pk):
    """Read the live progress JSON file for a download operation."""
    try:
        op = TraxDBOperation.objects.get(pk=pk, op_type='download')
    except TraxDBOperation.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if not op.progress_path or not os.path.exists(op.progress_path):
        return Response({
            'status': op.status,
            'progress': None,
        })

    try:
        with open(op.progress_path, 'r', encoding='utf-8') as f:
            progress = json.load(f)
    except (json.JSONDecodeError, IOError):
        progress = None

    return Response({
        'status': op.status,
        'progress': progress,
    })


@api_view(['POST'])
def cancel_download(request, pk):
    """Cancel a running download."""
    try:
        op = TraxDBOperation.objects.get(pk=pk, op_type='download')
    except TraxDBOperation.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if op.status != 'running':
        return Response(
            {'error': f'Download is not running (status: {op.status})'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Remove lock file to signal stop
    traxdb_root = get_config('TRAXDB_ROOT')
    lock_path = os.path.join(traxdb_root, '.download_from_report.lock')
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except OSError as e:
        logger.warning(f'Could not remove lock file: {e}')

    op.status = 'failed'
    op.error_message = 'Cancelled by user'
    op.save()

    return Response(TraxDBOperationSerializer(op).data)


# ── Scraped folders/tracks browsing ───────────────────────────

@api_view(['GET'])
def folders_list(request):
    """List scraped folders with filtering."""
    from django.db.models import Count, Q as DBQ
    # `.annotate()` turns this into a GROUP BY query, and since Django 3.1 that
    # silently drops `Meta.ordering` (['-scraped_at']). With no explicit
    # order_by the row order is undefined, and in practice Postgres returned
    # them oldest-first — so with the frontend's limit=100 over 160 folders,
    # freshly scraped lists fell off the end and the panel looked like sync had
    # never found them.
    #
    # Order by blog date rather than `-scraped_at` alone: one sync inserts a
    # batch newest-post-first, so scrape order runs *backwards* against the
    # dates the panel actually displays. `inferred_date` is an ISO string, so
    # lexical sort is chronological and blank dates land last.
    qs = ScrapedFolder.objects.annotate(
        tracks_count_annotated=Count('tracks'),
        tracks_downloaded_annotated=Count('tracks', filter=DBQ(tracks__downloaded=True)),
    ).order_by('-inferred_date', '-scraped_at', '-id')

    # Filter by download status. Comma-separated so the panel can pull the
    # whole in-flight queue (downloading + pending + failed) in one request
    # rather than three that can disagree with each other mid-cycle.
    dl_status = request.query_params.get('download_status')
    if dl_status:
        wanted = [s for s in (p.strip() for p in dl_status.split(',')) if s]
        if wanted:
            qs = qs.filter(download_status__in=wanted)

    # Filter by date range
    date_from = request.query_params.get('date_from')
    if date_from:
        qs = qs.filter(inferred_date__gte=date_from)
    date_to = request.query_params.get('date_to')
    if date_to:
        qs = qs.filter(inferred_date__lte=date_to)

    # Search by folder_id or title
    search = request.query_params.get('search')
    if search:
        qs = qs.filter(Q(folder_id__icontains=search) | Q(title__icontains=search))

    limit = int(request.query_params.get('limit', 100))
    offset = int(request.query_params.get('offset', 0))
    total = qs.count()
    qs = qs[offset:offset + limit]

    # Same context the inventory counts use, so a row's `queue_state` and the
    # chip counting it are computed from one set of facts.
    serializer = ScrapedFolderSerializer(qs, many=True, context=_queue_context())
    return Response({
        'results': serializer.data,
        'total': total,
        'limit': limit,
        'offset': offset,
    })


@api_view(['POST'])
def retry_failed_folders(request):
    """Put every failed list back in the queue.

    A failed download is nearly always a fixable outside cause — an expired
    Pixeldrain key, a dropped connection — and the folder is the atomic unit,
    so re-queuing is just "offer it to the daemon again". Without this the only
    way back was a Django shell on the VPS, and failures piled up unnoticed.
    Clears the lease too: a folder that failed while claimed keeps its token,
    and the daemon only re-offers unclaimed rows.
    """
    n = ScrapedFolder.objects.filter(download_status='failed').update(
        download_status='pending', claimed_at=None, claim_token='',
    )
    logger.info('Re-queued %d failed TraxDB folder(s)', n)
    return Response({'requeued': n})


@api_view(['GET'])
def folder_detail(request, pk):
    """Get a single folder with its tracks."""
    try:
        folder = ScrapedFolder.objects.get(pk=pk)
    except ScrapedFolder.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = ScrapedFolderDetailSerializer(folder)
    return Response(serializer.data)


@api_view(['GET'])
def folder_tracks(request, pk):
    """List tracks in a folder."""
    try:
        folder = ScrapedFolder.objects.get(pk=pk)
    except ScrapedFolder.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    tracks = folder.tracks.all()
    serializer = ScrapedTrackSerializer(tracks, many=True)
    return Response({'results': serializer.data})
