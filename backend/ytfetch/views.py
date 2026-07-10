import logging
import os

from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from core.services.config import get_config
from organize.auth import require_kick_token

from .models import FetchJob
from .serializers import FetchJobSerializer, CreateFetchSerializer
from .tasks import task_fetch, ingest_and_process

logger = logging.getLogger(__name__)


@api_view(['POST'])
def fetch(request):
    """Queue a YouTube URL for audio download into the organize pipeline."""
    ser = CreateFetchSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    job = FetchJob.objects.create(url=ser.validated_data['url'], status='queued')
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


# ─── Mac local-download fallback ──────────────────────────────────────────
# When YouTube bot-checks the VPS, run_fetch_job parks the job at
# 'needs_local' (see tasks._route_local_or_fail). The home Mac's daemon polls
# pending_local/, downloads on its residential IP, and posts the finished audio
# back to deliver_local/, which ingests it exactly like a VPS-side success.
# Both endpoints are gated by the same KICK_TOKEN bearer as pipeline/kick/.


@api_view(['GET'])
@require_kick_token
def pending_local(request):
    """List jobs waiting for a Mac local download (oldest first, up to 20)."""
    jobs = FetchJob.objects.filter(status='needs_local').order_by('id')[:20]
    return Response({
        'jobs': [
            {'id': j.id, 'url': j.url, 'title': j.title} for j in jobs
        ]
    })


# Decorator order matters: require_kick_token must be innermost. It wraps the
# view in a plain function that doesn't carry the parser_classes attribute, so
# @parser_classes has to sit above it for @api_view to see the multipart parser.
@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@require_kick_token
def deliver_local(request, pk):
    """Accept a Mac-downloaded audio file and ingest it into the pipeline.

    Multipart field `file` carries the audio; optional form field `filename`
    overrides the stored name. The file lands in 01_downloaded/YouTube/ and is
    fed through the organize pipeline identically to a VPS-side success.
    """
    try:
        job = FetchJob.objects.get(pk=pk)
    except FetchJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if job.status not in ('needs_local', 'fetching'):
        return Response(
            {'error': f'Job is {job.status}; only needs_local/fetching jobs '
                      'accept a local delivery.'},
            status=status.HTTP_409_CONFLICT,
        )

    upload = request.FILES.get('file')
    if upload is None:
        return Response(
            {'error': 'no file in multipart payload (expected "file")'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Strip any path components a caller might sneak in via filename/upload name.
    raw_name = request.data.get('filename') or upload.name or ''
    filename = os.path.basename(raw_name.strip())
    if not filename or filename in ('.', '..'):
        return Response(
            {'error': 'empty or invalid filename'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    target_dir = os.path.join(
        get_config('SOULSEEK_DOWNLOAD_ROOT'), '01_downloaded', 'YouTube'
    )
    os.makedirs(target_dir, exist_ok=True)

    dest_path = os.path.join(target_dir, filename)
    if os.path.exists(dest_path):
        # Don't clobber an existing file — disambiguate with the video/job id.
        stem, ext = os.path.splitext(filename)
        suffix = job.video_id or str(job.id)
        dest_path = os.path.join(target_dir, f'{stem} [{suffix}]{ext}')

    with open(dest_path, 'wb') as fh:
        for chunk in upload.chunks():
            fh.write(chunk)

    job.downloaded_path = dest_path
    job.status = 'downloaded'
    job.save(update_fields=['downloaded_path', 'status'])

    # Ingest is best-effort: bytes are safely on disk and the job is already
    # 'downloaded', so a pipeline hiccup won't lose the file (ingest_and_process
    # swallows its own errors and logs them).
    ingest_and_process(job, dest_path)

    return Response(FetchJobSerializer(job).data, status=status.HTTP_200_OK)
