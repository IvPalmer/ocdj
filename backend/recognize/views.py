import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import RecognizeJob
from .serializers import (
    RecognizeJobSerializer, RecognizeJobListSerializer, CreateJobSerializer,
)
from .services.pipeline import run_recognize

logger = logging.getLogger(__name__)


@api_view(['GET'])
def job_list(request):
    """List all recognition jobs."""
    qs = RecognizeJob.objects.all()

    limit = int(request.query_params.get('limit', 50))
    qs = qs[:limit]

    serializer = RecognizeJobListSerializer(qs, many=True)
    return Response({'results': serializer.data})


@api_view(['GET'])
def job_detail(request, pk):
    """Get a single recognition job with full tracklist."""
    try:
        job = RecognizeJob.objects.get(pk=pk)
    except RecognizeJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = RecognizeJobSerializer(job)
    return Response(serializer.data)


@api_view(['POST'])
def create_job(request):
    """Start a new recognition job."""
    ser = CreateJobSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    job = RecognizeJob.objects.create(
        url=ser.validated_data['url'],
    )

    # Launch pipeline in background thread
    run_recognize(job.id)

    serializer = RecognizeJobSerializer(job)
    return Response(serializer.data, status=status.HTTP_202_ACCEPTED)


@api_view(['POST'])
def add_to_wanted(request, pk):
    """Add selected tracks from a recognition job to the Wanted List."""
    try:
        job = RecognizeJob.objects.get(pk=pk)
    except RecognizeJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if job.status != 'completed':
        return Response(
            {'error': f'Job is not completed (status: {job.status})'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    track_indices = request.data.get('track_indices', [])
    if not track_indices:
        return Response(
            {'error': 'track_indices is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from wanted.models import WantedSource, WantedItem

    # Get or create a "recognize" source
    source, _ = WantedSource.objects.get_or_create(
        source_type='manual',
        name='Recognize',
        defaults={'url': job.url},
    )

    created_count = 0
    for idx in track_indices:
        if idx < 0 or idx >= len(job.tracklist):
            continue

        track = job.tracklist[idx]
        artist = track.get('artist', '')
        title = track.get('title', '')

        if not artist and not title:
            continue

        # Avoid duplicates
        exists = WantedItem.objects.filter(artist=artist, title=title).exists()
        if exists:
            continue

        WantedItem.objects.create(
            artist=artist,
            title=title,
            release_name=track.get('album', ''),
            label=track.get('label', ''),
            source=source,
            notes=f"From mix: {job.title or job.url}",
        )
        created_count += 1

    return Response({
        'created': created_count,
        'total_requested': len(track_indices),
    })
