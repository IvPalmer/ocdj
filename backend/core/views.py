from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status as http_status
from django.conf import settings

from .models import Config
from .services.config import (
    SCHEMA,
    categories,
    get_config,
    get_spec,
    list_specs,
    mask_value,
    set_config,
    source_of,
)


# Backwards-compat: callers outside core can still do `from core.views import get_config`.
__all__ = ['get_config']


# ── Config (key-value settings) ─────────────────────────────

@api_view(['GET'])
def config_list(request):
    """Return the full schema + current values (secrets masked)."""
    result = {}
    for spec in SCHEMA:
        raw = get_config(spec.key)
        if spec.type == 'bool':
            display_value = bool(raw)
        elif raw in ('', None):
            display_value = ''
        else:
            display_value = mask_value(str(raw), spec)
        result[spec.key] = {
            'set': _is_set(spec.key, spec),
            'value': display_value,
            'source': source_of(spec.key),
            'category': spec.category,
            'type': spec.type,
            'is_secret': spec.is_secret,
            'description': spec.description,
            'default': spec.default if not spec.is_secret else '',
        }
    return Response(result)


@api_view(['GET'])
def config_schema(request):
    """Metadata-only: schema grouped by category (for auto-rendering Settings UI)."""
    by_cat = {}
    for cat in categories():
        by_cat[cat] = [
            {
                'key': s.key,
                'type': s.type,
                'is_secret': s.is_secret,
                'description': s.description,
                'default': s.default if not s.is_secret else '',
            }
            for s in list_specs(cat)
        ]
    return Response({'categories': list(categories()), 'schema': by_cat})


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
                set_config(name_key, name)
        except Exception:
            pass
        finally:
            from django import db
            db.connections.close_all()

    threading.Thread(target=worker, daemon=True).start()


@api_view(['POST'])
def config_update(request):
    """Update one or more config values. Only keys in the schema are accepted."""
    updated = []
    for key, value in request.data.items():
        spec = get_spec(key)
        if spec is None:
            continue
        set_config(key, value)
        updated.append(key)

        if key in PLAYLIST_URL_TO_NAME_KEY and value:
            _resolve_playlist_name(key, value)

    if not updated:
        return Response(
            {'error': 'No valid keys provided'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    return Response({'updated': updated})


def _is_set(key: str, spec) -> bool:
    src = source_of(key)
    if src == 'unset':
        return False
    if src == 'default':
        # Non-trivial default (e.g. boolean False or empty string) counts as unset for UI.
        return bool(spec.default)
    return True


@api_view(['GET'])
def health(request):
    """Health check endpoint."""
    return Response({
        'status': 'ok',
        'slskd_url': get_config('SLSKD_BASE_URL'),
        'music_root': get_config('MUSIC_ROOT'),
        'soulseek_root': get_config('SOULSEEK_DOWNLOAD_ROOT'),
        'traxdb_root': get_config('TRAXDB_ROOT'),
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


# ── Automation ────────────────────────────────────────────────

@api_view(['POST'])
def automation_run(request):
    from .services.automation import run_automation_cycle
    dry_run = request.data.get('dry_run', False)
    report = run_automation_cycle(dry_run=dry_run)
    return Response(report)


@api_view(['GET', 'POST'])
def automation_config(request):
    from .services.automation import get_automation_config, set_automation_config

    if request.method == 'GET':
        return Response(get_automation_config())

    updated = set_automation_config(request.data)
    if not updated:
        return Response(
            {'error': 'No valid keys provided'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )
    return Response({'updated': updated, 'config': get_automation_config()})


@api_view(['POST'])
def audit_music_root(request):
    """HTTP wrapper around the audit_music_root management command.

    Runs the command in-process and captures the stdout report. Defaults to
    dry-run; pass `apply: true` to execute. `reclassify: ["folder", ...]`
    opts user-content folders into the sweep.
    """
    import io
    from django.core.management import call_command

    apply_changes = bool(request.data.get('apply', False))
    reclassify = request.data.get('reclassify') or []
    if isinstance(reclassify, str):
        reclassify = [reclassify]

    buf = io.StringIO()
    try:
        kwargs = {'stdout': buf, 'stderr': buf}
        if apply_changes:
            kwargs['apply'] = True
        if reclassify:
            kwargs['reclassify'] = reclassify
        call_command('audit_music_root', **kwargs)
        return Response({
            'ok': True,
            'apply': apply_changes,
            'reclassify': reclassify,
            'report': buf.getvalue(),
        })
    except Exception as e:
        return Response(
            {'ok': False, 'error': str(e), 'report': buf.getvalue()},
            status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['GET'])
def automation_status(request):
    from .services.automation import run_automation_cycle, get_automation_config, get_pipeline_status

    config = get_automation_config()
    pipeline = get_pipeline_status()
    preview = run_automation_cycle(dry_run=True)

    return Response({
        'config': config,
        'pipeline': pipeline,
        'preview': preview,
    })
