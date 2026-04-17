import logging
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import RecognizeJob
from .serializers import (
    RecognizeJobSerializer, RecognizeJobListSerializer, CreateJobSerializer,
)
from .services.pipeline import run_recognize
from .services.trackid import lookup_by_url

logger = logging.getLogger(__name__)


@api_view(['GET'])
def job_list(request):
    """List all recognition jobs."""
    qs = RecognizeJob.objects.all().order_by('-created')

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


@api_view(['POST'])
def resume_job(request, pk):
    """Resume a stuck job (downloading/recognizing) that was interrupted."""
    try:
        job = RecognizeJob.objects.get(pk=pk)
    except RecognizeJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if job.status not in ('downloading', 'recognizing'):
        return Response(
            {'error': f'Job is {job.status}, not stuck'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    run_recognize(job.id)
    return Response({'message': f'Resuming job {job.id}'})


@api_view(['POST'])
def rerun_job(request, pk):
    """Re-run recognition on a completed or failed job from scratch."""
    try:
        job = RecognizeJob.objects.get(pk=pk)
    except RecognizeJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if job.status in ('downloading', 'recognizing'):
        return Response(
            {'error': f'Job is already running ({job.status})'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Reset job state for a fresh run
    job.status = 'pending'
    job.tracklist = []
    job.raw_results = []
    job.description_tracks = []
    job.segments_total = 0
    job.segments_done = 0
    job.tracks_found = 0
    job.acrcloud_calls = 0
    job.error_message = ''
    job.engine = 'shazam'
    job.save()

    run_recognize(job.id)

    serializer = RecognizeJobSerializer(job)
    return Response(serializer.data, status=status.HTTP_202_ACCEPTED)


@api_view(['DELETE'])
def delete_job(request, pk):
    """Delete a recognition job."""
    try:
        job = RecognizeJob.objects.get(pk=pk)
    except RecognizeJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    job.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
def recluster_job(request, pk):
    """Re-cluster a completed job's raw_results with current clustering algorithm."""
    try:
        job = RecognizeJob.objects.get(pk=pk)
    except RecognizeJob.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if not job.raw_results:
        return Response(
            {'error': 'No raw results to re-cluster'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from .services.clustering import cluster_results, dedup_tracklist
    from .services.pipeline import _merge_trackid_results
    tracklist = cluster_results(job.raw_results, job.description_tracks)

    # Also merge TrackID.net results if available
    try:
        trackid_result = lookup_by_url(job.url)
        if trackid_result and trackid_result.get('tracklist'):
            tracklist = _merge_trackid_results(tracklist, trackid_result['tracklist'])
    except Exception as e:
        logger.warning(f'TrackID.net lookup failed during recluster for job {pk}: {e}')

    tracklist = dedup_tracklist(tracklist)

    # Update engine based on merged sources
    engines_used = set()
    for t in tracklist:
        engines_used.update(t.get('engines', []))
    if len(engines_used) > 1:
        job.engine = 'hybrid'
    elif 'trackid' in engines_used:
        job.engine = 'trackid'

    job.tracklist = tracklist
    job.tracks_found = len(tracklist)
    job.save()

    serializer = RecognizeJobSerializer(job)
    return Response(serializer.data)


@api_view(['POST'])
def trackid_lookup(request):
    """Look up a mix URL on TrackID.net and return any existing tracklist."""
    url = request.data.get('url', '').strip()
    if not url:
        return Response(
            {'error': 'url is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    result = lookup_by_url(url)
    if not result:
        return Response({'found': False})

    return Response({
        'found': True,
        'title': result.get('title', ''),
        'duration_seconds': result.get('duration_seconds', 0),
        'trackid_status': result.get('trackid_status', ''),
        'tracklist': result.get('tracklist', []),
        'track_count': len(result.get('tracklist', [])),
    })


@api_view(['GET'])
def acrcloud_usage(request):
    """Return ACRCloud API usage stats — local tracking + Console API for plan limits."""
    import requests as http_requests
    from core.views import get_config

    configured = bool(get_config('ACRCLOUD_ACCESS_KEY') and get_config('ACRCLOUD_ACCESS_SECRET'))
    bearer_token = get_config('ACRCLOUD_BEARER_TOKEN')

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Local tracking (fallback when no bearer token)
    # Filter by 'updated' not 'created' — a job created yesterday but processed
    # today should count its ACRCloud calls against today's usage
    local_total = RecognizeJob.objects.aggregate(total=Sum('acrcloud_calls'))['total'] or 0
    local_today = RecognizeJob.objects.filter(
        updated__gte=today_start,
        acrcloud_calls__gt=0,
    ).aggregate(total=Sum('acrcloud_calls'))['total'] or 0
    local_month = RecognizeJob.objects.filter(
        updated__gte=month_start,
        acrcloud_calls__gt=0,
    ).aggregate(total=Sum('acrcloud_calls'))['total'] or 0

    result = {
        'configured': configured,
        'has_console_api': bool(bearer_token),
        'local_calls_total': local_total,
        'local_calls_today': local_today,
        'local_calls_month': local_month,
        # Plan details — filled from Console API if available
        'plan': None,
    }

    if bearer_token:
        try:
            headers = {'Authorization': f'Bearer {bearer_token}'}

            # Fetch project list to get limits
            resp = http_requests.get(
                'https://api-v2.acrcloud.com/api/base-projects',
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                body = resp.json()
                projects = body.get('data', []) if isinstance(body, dict) else body

                # Find the project matching our access key
                access_key = get_config('ACRCLOUD_ACCESS_KEY')
                project = None
                for p in projects:
                    if p.get('access_key') == access_key:
                        project = p
                        break
                if not project and projects:
                    project = projects[0]

                if project:
                    day_limit = project.get('day_limit', 0)

                    # Fetch daily stats for the current month
                    start_date = month_start.strftime('%Y-%m-%d')
                    end_date = now.strftime('%Y-%m-%d')
                    stat_resp = http_requests.get(
                        f'https://api-v2.acrcloud.com/api/base-projects/{project["id"]}/day-stat',
                        headers=headers,
                        params={'start': start_date, 'end': end_date},
                        timeout=10,
                    )

                    api_today = 0
                    api_month = 0
                    valid_today = 0
                    valid_month = 0
                    if stat_resp.status_code == 200:
                        stat_body = stat_resp.json()
                        stats = stat_body.get('data', []) if isinstance(stat_body, dict) else stat_body
                        today_str = now.strftime('%Y-%m-%d')
                        for entry in stats:
                            results_count = entry.get('result', 0)
                            noresult_count = entry.get('noresult', 0)
                            error_count = entry.get('error', 0)
                            raw = results_count + noresult_count + error_count
                            # ACRCloud billing: valid = results + (noresults / 2)
                            valid = results_count + (noresult_count / 2)
                            api_month += raw
                            valid_month += valid
                            if entry.get('date') == today_str:
                                api_today = raw
                                valid_today = valid

                    # Cost estimate based on project config
                    # Base $3/1000 + 80% for ACRCloud Music bucket
                    # + 20% if 3rd party IDs enabled
                    has_external_ids = bool(project.get('external_ids'))
                    rate_per_1000 = 3.00 * 1.8  # base + music bucket
                    if has_external_ids:
                        rate_per_1000 = 3.00 * 2.0  # + 3rd party IDs
                    est_cost_month = round(valid_month / 1000 * rate_per_1000, 2)

                    result['plan'] = {
                        'name': project.get('name', ''),
                        'day_limit': day_limit,  # 0 = unlimited (trial)
                        'calls_today': api_today,
                        'calls_month': api_month,
                        'valid_today': valid_today,
                        'valid_month': valid_month,
                        'remaining_today': max(0, day_limit - api_today) if day_limit > 0 else None,
                        'is_trial': day_limit == 0,
                        'rate_per_1000': rate_per_1000,
                        'est_cost_month': est_cost_month,
                    }
        except Exception as e:
            logger.warning(f'ACRCloud Console API error: {e}')

    return Response(result)
