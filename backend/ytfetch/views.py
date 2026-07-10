import logging
import os
import threading

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
from rest_framework.decorators import parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import FetchJob
from .serializers import FetchJobSerializer, CreateFetchSerializer
from .tasks import task_fetch
from .auth import require_youtube_worker_token

logger = logging.getLogger(__name__)


def _uses_local_worker():
    return os.environ.get('YOUTUBE_FETCH_MODE', 'server').strip().lower() == 'local'


@api_view(['POST'])
def fetch(request):
    """Queue a YouTube URL for audio download into the organize pipeline."""
    ser = CreateFetchSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    job = FetchJob.objects.create(url=ser.validated_data['url'], status='queued')
    if not _uses_local_worker():
        task_fetch(job.id)

    return Response(FetchJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
def jobs(request):
    """List the 50 most recent fetch jobs."""
    qs = FetchJob.objects.all()[:50]
    return Response({'results': FetchJobSerializer(qs, many=True).data})


@api_view(['POST'])
def retry_job(request, pk):
    """Re-queue a failed job. No-op path for jobs in any other state."""
    try:
        job = FetchJob.objects.get(pk=pk)
    except FetchJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if job.status != 'failed':
        return Response(
            {'error': f'Only failed jobs can be retried (status: {job.status}).'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    job.status = 'queued'
    job.error_message = ''
    job.save(update_fields=['status', 'error_message'])
    if not _uses_local_worker():
        task_fetch(job.id)

    return Response(FetchJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


@api_view(['DELETE'])
def delete_job(request, pk):
    """Delete a job row only. Never touches files — the pipeline owns those."""
    try:
        job = FetchJob.objects.get(pk=pk)
    except FetchJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    # Deleting a queued/fetching row would orphan the in-flight Huey task —
    # it would still download the file but have no job to report into.
    if job.status not in ('downloaded', 'failed'):
        return Response(
            {'error': f'Cannot remove a job while it is {job.status}; '
                      'wait for it to finish or fail.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    job.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@require_youtube_worker_token
def worker_claim(request):
    """Atomically hand one queued job to the local no-cookie downloader."""
    if not _uses_local_worker():
        return Response({'error': 'local worker mode is disabled'}, status=status.HTTP_409_CONFLICT)

    stale_before = timezone.now() - timedelta(minutes=30)
    with transaction.atomic():
        job = (
            FetchJob.objects.select_for_update()
            .filter(Q(status='queued') | Q(status='fetching', updated__lt=stale_before))
            .order_by('created')
            .first()
        )
        if job is None:
            return Response({'job': None})
        job.status = 'fetching'
        job.error_message = ''
        job.save(update_fields=['status', 'error_message', 'updated'])
    return Response({'job': FetchJobSerializer(job).data})


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@require_youtube_worker_token
def worker_complete(request, pk):
    """Accept a local worker download and start the normal organizer pipeline."""
    try:
        job = FetchJob.objects.get(pk=pk)
    except FetchJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if job.status not in ('queued', 'fetching'):
        return Response({'error': f'Job is already {job.status}'}, status=status.HTTP_409_CONFLICT)

    uploaded = request.FILES.get('file')
    if uploaded is None:
        return Response({'error': 'multipart field "file" is required'}, status=status.HTTP_400_BAD_REQUEST)
    ext = os.path.splitext(uploaded.name)[1].lower()
    if ext not in {'.wav', '.aiff', '.aif', '.flac', '.mp3', '.m4a', '.ogg'}:
        return Response({'error': f'unsupported audio extension: {ext}'}, status=status.HTTP_400_BAD_REQUEST)

    from organize.models import PipelineItem
    from organize.services.pipeline import write_uploaded_file_to_downloaded

    dest_path = write_uploaded_file_to_downloaded(uploaded)
    item = PipelineItem.objects.create(
        original_filename=os.path.basename(dest_path),
        current_path=dest_path,
        stage='downloaded',
        archive_state='on_workbench',
        metadata_source='youtube-local',
    )
    for field in ('video_id', 'uploader', 'title'):
        value = str(request.data.get(field) or '').strip()
        if value:
            setattr(job, field, value[:500 if field != 'video_id' else 32])
    job.downloaded_path = dest_path
    job.pipeline_item = item
    job.status = 'downloaded'
    job.error_message = ''
    job.save(update_fields=['video_id', 'uploader', 'title', 'downloaded_path', 'pipeline_item', 'status', 'error_message'])

    from organize.services.pipeline import process_pipeline_item
    threading.Thread(target=process_pipeline_item, args=(item.id,), daemon=True).start()
    return Response(FetchJobSerializer(job).data, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@parser_classes([FormParser])
@require_youtube_worker_token
def worker_fail(request, pk):
    """Record a local worker failure without exposing raw credentials or logs."""
    try:
        job = FetchJob.objects.get(pk=pk)
    except FetchJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
    if job.status not in ('queued', 'fetching'):
        return Response({'error': f'Job is already {job.status}'}, status=status.HTTP_409_CONFLICT)
    message = str(request.data.get('error') or 'Local YouTube worker failed').strip()
    job.status = 'failed'
    job.error_message = message[-1000:]
    job.save(update_fields=['status', 'error_message'])
    return Response(FetchJobSerializer(job).data)
