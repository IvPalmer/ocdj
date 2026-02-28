import json
import logging
import os
import re
import threading

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import TraxDBOperation
from .serializers import (
    TraxDBOperationSerializer,
    TriggerSyncSerializer,
    TriggerDownloadSerializer,
    TriggerAuditSerializer,
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

    return Response({
        'date_dirs_count': len(date_dirs),
        'latest_date': date_dirs[-1] if date_dirs else None,
        'oldest_date': date_dirs[0] if date_dirs else None,
        'known_lists_count': len(seen_ids),
        'file_count': file_count,
        'total_bytes': total_bytes,
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

    # Simple limit (no DRF pagination for now, matches soulseek pattern)
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
    # Check for already-running sync
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
    """Trigger a download operation from a sync report."""
    # Check for already-running download
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
    if sync_op_id:
        try:
            sync_op = TraxDBOperation.objects.get(id=sync_op_id, op_type='sync', status='completed')
            sync_report_path = sync_op.report_path
        except TraxDBOperation.DoesNotExist:
            return Response(
                {'error': f'Sync operation {sync_op_id} not found or not completed'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        sync_report_path = _get_latest_sync_report()

    if not sync_report_path:
        return Response(
            {'error': 'No completed sync report available. Run a sync first.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    op = TraxDBOperation.objects.create(op_type='download')

    thread = threading.Thread(
        target=run_download,
        args=(op.id, sync_report_path),
        kwargs={'links_key': links_key},
        daemon=True,
    )
    thread.start()

    return Response(
        TraxDBOperationSerializer(op).data,
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['POST'])
def trigger_audit(request):
    """Trigger an audit operation against a sync report."""
    # Check for already-running audit
    if TraxDBOperation.objects.filter(op_type='audit', status='running').exists():
        return Response(
            {'error': 'An audit is already running'},
            status=status.HTTP_409_CONFLICT,
        )

    ser = TriggerAuditSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    # Determine which sync report to use
    sync_op_id = ser.validated_data.get('sync_operation_id')
    if sync_op_id:
        try:
            sync_op = TraxDBOperation.objects.get(id=sync_op_id, op_type='sync', status='completed')
            sync_report_path = sync_op.report_path
        except TraxDBOperation.DoesNotExist:
            return Response(
                {'error': f'Sync operation {sync_op_id} not found or not completed'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        sync_report_path = _get_latest_sync_report()

    if not sync_report_path:
        return Response(
            {'error': 'No completed sync report available. Run a sync first.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    op = TraxDBOperation.objects.create(op_type='audit')

    thread = threading.Thread(
        target=run_audit,
        args=(op.id, sync_report_path),
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
    """Cancel a running download by removing the lock file."""
    try:
        op = TraxDBOperation.objects.get(pk=pk, op_type='download')
    except TraxDBOperation.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if op.status != 'running':
        return Response(
            {'error': f'Download is not running (status: {op.status})'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Remove lock file to signal the CLI tool to stop
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
