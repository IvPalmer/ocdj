from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.conf import settings


@api_view(['GET'])
def health(request):
    """Health check endpoint."""
    return Response({
        'status': 'ok',
        'slskd_url': settings.SLSKD_BASE_URL,
        'music_root': settings.MUSIC_ROOT,
    })


@api_view(['GET'])
def stats(request):
    """Dashboard stats."""
    from wanted.models import WantedItem
    from django.db.models import Count

    status_counts = dict(
        WantedItem.objects.values_list('status')
        .annotate(count=Count('id'))
        .values_list('status', 'count')
    )

    return Response({
        'wanted': {
            'total': sum(status_counts.values()),
            'pending': status_counts.get('pending', 0),
            'searching': status_counts.get('searching', 0),
            'downloading': status_counts.get('downloading', 0),
            'downloaded': status_counts.get('downloaded', 0),
            'failed': status_counts.get('failed', 0),
        }
    })
