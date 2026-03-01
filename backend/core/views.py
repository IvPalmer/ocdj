from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status as http_status
from django.conf import settings

from .models import Config


# ── Config (key-value settings) ─────────────────────────────

# Keys that can be stored/read via the API
ALLOWED_CONFIG_KEYS = [
    'YOUTUBE_API_KEY',
    'YOUTUBE_DEFAULT_PLAYLIST',
    'SC_CLIENT_ID',
    'SC_CLIENT_SECRET',
    'SC_DEFAULT_PLAYLIST',
    'SPOTIFY_CLIENT_ID',
    'SPOTIFY_CLIENT_SECRET',
    'SPOTIFY_REDIRECT_URI',
    'SPOTIFY_DEFAULT_PLAYLIST',
    'DISCOGS_PERSONAL_TOKEN',
    'DISCOGS_USERNAME',
]


def get_config(key, default=''):
    """Get a config value: DB first, then env var, then default."""
    try:
        return Config.objects.get(key=key).value
    except Config.DoesNotExist:
        import os
        return os.environ.get(key, '') or getattr(settings, key, default)


NON_SECRET_KEYS = {
    'YOUTUBE_DEFAULT_PLAYLIST',
    'SC_DEFAULT_PLAYLIST',
    'SPOTIFY_DEFAULT_PLAYLIST',
    'SPOTIFY_REDIRECT_URI',
    'DISCOGS_USERNAME',
}


@api_view(['GET'])
def config_list(request):
    """Return all configurable settings with current values (secrets masked)."""
    result = {}
    for key in ALLOWED_CONFIG_KEYS:
        value = get_config(key)
        result[key] = {
            'set': bool(value),
            'value': value if key in NON_SECRET_KEYS else (_mask(value) if value else ''),
            'source': _source(key),
        }
    return Response(result)


@api_view(['POST'])
def config_update(request):
    """Update one or more config values."""
    updated = []
    for key, value in request.data.items():
        if key not in ALLOWED_CONFIG_KEYS:
            continue
        Config.objects.update_or_create(
            key=key,
            defaults={'value': value},
        )
        updated.append(key)

    if not updated:
        return Response(
            {'error': 'No valid keys provided'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    return Response({'updated': updated})


def _mask(value):
    """Mask a secret value, showing only first 4 and last 4 chars."""
    if not value or len(value) <= 10:
        return '*' * len(value) if value else ''
    return value[:4] + '*' * (len(value) - 8) + value[-4:]


def _source(key):
    """Where the config value comes from."""
    try:
        Config.objects.get(key=key)
        return 'db'
    except Config.DoesNotExist:
        import os
        if os.environ.get(key):
            return 'env'
        if getattr(settings, key, ''):
            return 'settings'
        return 'unset'


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
