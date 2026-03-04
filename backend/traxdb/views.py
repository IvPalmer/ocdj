import json
import logging
import os
import re
import threading

from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import TraxDBOperation, ScrapedFolder, ScrapedTrack
from .serializers import (
    TraxDBOperationSerializer,
    TriggerSyncSerializer,
    TriggerDownloadSerializer,
    TriggerAuditSerializer,
    ScrapedFolderSerializer,
    ScrapedFolderDetailSerializer,
    ScrapedTrackSerializer,
)
from .services import run_sync, run_download, run_audit

logger = logging.getLogger(__name__)


# ── Local inventory ──────────────────────────────────────────

@api_view(['GET'])
def inventory(request):
    """Return local TraxDB inventory stats (date dirs, file counts, known lists)."""
    traxdb_root = os.environ.get('TRAXDB_ROOT', '/music/Electronic/ID3/traxdb')

    date_dirs = []
    file_count = 0
    total_bytes = 0

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

    # Read the seen-list IDs file
    seen_path = os.path.join(traxdb_root, '.pixeldrain_lists_seen.json')
    seen_ids = []
    try:
        with open(seen_path, 'r', encoding='utf-8') as f:
            seen_ids = json.load(f)
            if not isinstance(seen_ids, list):
                seen_ids = []
    except Exception:
        pass

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

    limit = int(request.query_params.get('limit', 50))
    qs = qs[:limit]

    serializer = TraxDBOperationSerializer(qs, many=True)
    return Response({'results': serializer.data})


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
    if TraxDBOperation.objects.filter(op_type='sync', status='running').exists():
        return Response(
            {'error': 'A sync is already running'},
            status=status.HTTP_409_CONFLICT,
        )

    ser = TriggerSyncSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    max_pages = ser.validated_data.get('max_pages', 50)

    op = TraxDBOperation.objects.create(op_type='sync')

    thread = threading.Thread(
        target=run_sync,
        args=(op.id,),
        kwargs={'max_pages': max_pages},
        daemon=True,
    )
    thread.start()

    return Response(
        TraxDBOperationSerializer(op).data,
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['POST'])
def trigger_download(request):
    """Trigger a download operation."""
    if TraxDBOperation.objects.filter(op_type='download', status='running').exists():
        return Response(
            {'error': 'A download is already running'},
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

    op = TraxDBOperation.objects.create(op_type='download')

    thread = threading.Thread(
        target=run_download,
        args=(op.id,),
        kwargs={'sync_report_path': sync_report_path, 'links_key': links_key},
        daemon=True,
    )
    thread.start()

    return Response(
        TraxDBOperationSerializer(op).data,
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['POST'])
def trigger_audit(request):
    """Trigger an audit operation."""
    if TraxDBOperation.objects.filter(op_type='audit', status='running').exists():
        return Response(
            {'error': 'An audit is already running'},
            status=status.HTTP_409_CONFLICT,
        )

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

    op = TraxDBOperation.objects.create(op_type='audit')

    thread = threading.Thread(
        target=run_audit,
        args=(op.id,),
        kwargs={'sync_report_path': sync_report_path},
        daemon=True,
    )
    thread.start()

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
    traxdb_root = os.environ.get('TRAXDB_ROOT', '/music/Electronic/ID3/traxdb')
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
    qs = ScrapedFolder.objects.all()

    # Filter by download status
    dl_status = request.query_params.get('download_status')
    if dl_status:
        qs = qs.filter(download_status=dl_status)

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

    serializer = ScrapedFolderSerializer(qs, many=True)
    return Response({
        'results': serializer.data,
        'total': total,
        'limit': limit,
        'offset': offset,
    })


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
