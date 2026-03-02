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
    'YOUTUBE_DEFAULT_PLAYLIST_NAME',
    'SC_CLIENT_ID',
    'SC_CLIENT_SECRET',
    'SC_DEFAULT_PLAYLIST',
    'SC_DEFAULT_PLAYLIST_NAME',
    'SPOTIFY_CLIENT_ID',
    'SPOTIFY_CLIENT_SECRET',
    'SPOTIFY_REDIRECT_URI',
    'SPOTIFY_DEFAULT_PLAYLIST',
    'SPOTIFY_DEFAULT_PLAYLIST_NAME',
    'DISCOGS_PERSONAL_TOKEN',
    'DISCOGS_USERNAME',
    'ORGANIZE_RENAME_TEMPLATE',
    'TRACKID_TOKEN',
    'ACRCLOUD_ACCESS_KEY',
    'ACRCLOUD_ACCESS_SECRET',
    'ACRCLOUD_HOST',
    'ACRCLOUD_BEARER_TOKEN',
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
    'YOUTUBE_DEFAULT_PLAYLIST_NAME',
    'SC_DEFAULT_PLAYLIST',
    'SC_DEFAULT_PLAYLIST_NAME',
    'SPOTIFY_DEFAULT_PLAYLIST',
    'SPOTIFY_DEFAULT_PLAYLIST_NAME',
    'SPOTIFY_REDIRECT_URI',
    'DISCOGS_USERNAME',
    'ORGANIZE_RENAME_TEMPLATE',
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


PLAYLIST_URL_TO_NAME_KEY = {
    'YOUTUBE_DEFAULT_PLAYLIST': 'YOUTUBE_DEFAULT_PLAYLIST_NAME',
    'SC_DEFAULT_PLAYLIST': 'SC_DEFAULT_PLAYLIST_NAME',
    'SPOTIFY_DEFAULT_PLAYLIST': 'SPOTIFY_DEFAULT_PLAYLIST_NAME',
}


def _resolve_playlist_name(url_key, url_value):
    """Resolve a playlist name from its URL in a background thread."""
    import threading

    name_key = PLAYLIST_URL_TO_NAME_KEY.get(url_key)
    if not name_key or not url_value:
        return

    def worker():
        try:
            name = ''
            if url_key == 'SPOTIFY_DEFAULT_PLAYLIST':
                import re
                match = re.search(r'playlist[/:]([a-zA-Z0-9]+)', url_value)
                if match:
                    from wanted.services.spotify import _get_sp
                    sp, _ = _get_sp()
                    info = sp.playlist(match.group(1), fields='name')
                    name = info.get('name', '')
            else:
                import yt_dlp
                opts = {'quiet': True, 'no_warnings': True, 'extract_flat': 'in_playlist', 'skip_download': True}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url_value, download=False)
                name = info.get('title', '')

            if name:
                Config.objects.update_or_create(key=name_key, defaults={'value': name})
        except Exception:
            pass
        finally:
            from django import db
            db.connections.close_all()

    threading.Thread(target=worker, daemon=True).start()


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

        # Auto-resolve playlist name when a playlist URL is saved
        if key in PLAYLIST_URL_TO_NAME_KEY and value:
            _resolve_playlist_name(key, value)

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

    # Pipeline stats
    from organize.models import PipelineItem
    pipeline_counts = dict(
        PipelineItem.objects.values_list('stage')
        .annotate(count=Count('id'))
        .values_list('stage', 'count')
    )

    return Response({
        'wanted': {
            'total': sum(status_counts.values()),
            'pending': status_counts.get('pending', 0),
            'searching': status_counts.get('searching', 0),
            'downloading': status_counts.get('downloading', 0),
            'downloaded': status_counts.get('downloaded', 0),
            'failed': status_counts.get('failed', 0),
        },
        'pipeline': {
            'total': sum(pipeline_counts.values()),
            'downloaded': pipeline_counts.get('downloaded', 0),
            'tagged': pipeline_counts.get('tagged', 0),
            'ready': pipeline_counts.get('ready', 0),
            'failed': pipeline_counts.get('failed', 0),
        },
    })
