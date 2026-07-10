import logging
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import FetchJob
from .serializers import FetchJobSerializer, CreateFetchSerializer
from .tasks import task_fetch

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
