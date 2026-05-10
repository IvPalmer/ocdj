"""Cratemate DRF views — port of crate-mate's FastAPI routes.

Maps:
    POST /api/cratemate/identify/   ←  POST /api/upload         (image → enriched album)
    POST /api/cratemate/lookup/     ←  GET  /api/metadata/...   (manual artist+album)
    GET  /api/cratemate/status/     ←  GET  /api/universal/status
    GET  /api/cratemate/results/    ←  list AlbumIdentification rows
    GET  /api/cratemate/results/<pk>/  ← detail
"""
import asyncio
import hashlib
import io
import logging
import time

from PIL import Image
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .models import AlbumIdentification, IdentifiedRelease, RecognitionRun
from .serializers import (
    AlbumIdentificationSerializer,
    IdentifyRequestSerializer,
    ManualLookupSerializer,
    RecognitionRunSerializer,
)

logger = logging.getLogger(__name__)


def _hybrid_searcher():
    """Lazy import — heavy graph (PIL + claude-agent-sdk + fuzzywuzzy)
    pulled in only when an /identify request arrives, so Django boot stays fast.

    Cached on the function attribute so subsequent requests reuse the
    in-memory cache the searcher keeps.
    """
    if not hasattr(_hybrid_searcher, '_instance'):
        from .services.hybrid_search import HybridSearch
        try:
            _hybrid_searcher._instance = HybridSearch()
        except Exception as e:
            logger.error("HybridSearch init failed: %s", e)
            _hybrid_searcher._instance = None
    return _hybrid_searcher._instance


def _credentials_configured():
    """Lightweight env check — used by views to bail out with 503 before
    booting the heavy hybrid searcher when nothing's set.

    V2 path: Claude Agent SDK via the operator's Max subscription. Same env
    var that organize/services/agent_enrich.py already requires."""
    import os
    key = os.getenv('CLAUDE_CODE_OAUTH_TOKEN', '')
    return bool(key) and key != '__PENDING__'


def _has(env_var: str) -> bool:
    """True if env var is set and not the placeholder."""
    import os
    v = os.getenv(env_var, '')
    return bool(v) and v != '__PENDING__'


@api_view(['GET'])
def status_view(request):
    """Operational status of the cratemate module."""
    return Response({
        'status': 'operational' if _credentials_configured() else 'unconfigured',
        'recognition_backend': 'claude_agent_sdk',
        'features': {
            'claude_vision': _has('CLAUDE_CODE_OAUTH_TOKEN'),
            'discogs': _has('CRATEMATE_DISCOGS_TOKEN'),
            'spotify': _has('CRATEMATE_SPOTIFY_CLIENT_ID'),
            'youtube': _has('CRATEMATE_YOUTUBE_API_KEY') or _has('YOUTUBE_API_KEY'),
        },
        'description': (
            'Album-cover identification (Claude vision via Max OAuth) + '
            'multi-platform metadata enrichment (Discogs, Spotify, YouTube, Bandcamp).'
        ),
    })


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
def identify(request):
    """Identify an album from an uploaded cover image.

    Returns 503 if credentials aren't configured (Phase 1 not yet run).
    Returns 200 with enriched metadata + the AlbumIdentification record on success.
    """
    if not _credentials_configured():
        return Response(
            {
                'error': 'cratemate credentials not configured',
                'detail': (
                    'Set CLAUDE_CODE_OAUTH_TOKEN in the backend environment '
                    '(generated once via `claude setup-token`). The same token '
                    'agent_enrich.py uses for filename parsing.'
                ),
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    ser = IdentifyRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    image_file = ser.validated_data['image']

    # Hash the input bytes so repeated uploads of the same crop dedup nicely.
    raw_bytes = image_file.read()
    image_hash = hashlib.md5(raw_bytes).hexdigest()

    try:
        image = Image.open(io.BytesIO(raw_bytes))
    except Exception as e:
        return Response(
            {'error': 'invalid image', 'detail': str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Stash a copy of every uploaded image to /tmp/cratemate-debug/ so failed
    # identifications can be inspected after the fact (e.g. via SSH).
    # Bounded to the last 50 files so we don't fill the container disk; the
    # filename includes timestamp + hash so it's easy to correlate with
    # AlbumIdentification rows in the DB.
    _stash_debug_image(raw_bytes, image_hash)

    searcher = _hybrid_searcher()
    if searcher is None:
        return Response(
            {'error': 'hybrid search unavailable', 'detail': 'See server logs.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    run = RecognitionRun.objects.create(status='running')
    started = time.time()

    try:
        # HybridSearch.search_album is async — drive it from the sync view
        # via asyncio.run. For V1 traffic levels this is fine; in V2 swap
        # to a Huey task if request volume warrants.
        result = asyncio.run(searcher.search_album(image))
    except Exception as e:
        logger.error("identify failed for hash=%s: %s", image_hash, e, exc_info=True)
        run.status = 'failed'
        run.error_message = str(e)
        run.duration_ms = int((time.time() - started) * 1000)
        run.save()
        return Response(
            {'error': 'identification failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    duration_ms = int((time.time() - started) * 1000)

    # Persist the identification + (optional) enriched release.
    #
    # NB: hybrid_search.py returns a NESTED payload — `album.*`, `links.*`,
    # `tracks.*`, `identification.*` — not the flat top-level fields the
    # original Gemini-era port assumed. The helper below normalizes both shapes
    # so old responses (if any leaked into the DB) and new ones survive.
    flat = _flatten_search_result(result) if isinstance(result, dict) else {}
    identification = AlbumIdentification.objects.create(
        image_hash=image_hash,
        method='claude_vision',
        raw_response=result if isinstance(result, dict) else {},
        artist_guess=flat.get('artist') or '',
        album_guess=flat.get('album') or '',
        confidence=flat.get('confidence_numeric'),
        error_message=(result or {}).get('error', '') if isinstance(result, dict) else '',
    )

    release = None
    if isinstance(result, dict) and not result.get('error') and (flat.get('artist') or flat.get('album')):
        release = IdentifiedRelease.objects.create(
            identification=identification,
            artist=flat.get('artist') or '',
            album=flat.get('album') or '',
            release_date=flat.get('release_date') or '',
            genres=flat.get('genres') or [],
            cover_image_url=flat.get('cover_image_url') or '',
            discogs_url=flat.get('discogs_url') or '',
            spotify_url=flat.get('spotify_url') or '',
            youtube_url=flat.get('youtube_url') or '',
            bandcamp_url=flat.get('bandcamp_url') or '',
            tracklist=flat.get('tracklist') or [],
            market_stats=flat.get('market_stats') or {},
            extra=flat.get('extra') or {},
        )

    run.identification = identification
    run.release = release
    run.status = 'completed' if release else 'failed'
    run.duration_ms = duration_ms
    run.error_message = identification.error_message
    run.save()

    # Return the original hybrid_search payload (frontend already understands it),
    # plus a tiny `cratemate` envelope with our DB ids for follow-up calls.
    payload = dict(result) if isinstance(result, dict) else {'error': 'unexpected result'}
    payload['cratemate'] = {
        'identification_id': identification.id,
        'release_id': release.id if release else None,
        'run_id': run.id,
        'duration_ms': duration_ms,
    }
    return Response(payload, status=status.HTTP_200_OK)


@api_view(['POST'])
def lookup(request):
    """Manual artist+album fallback when image identification isn't useful.

    Skips the vision step entirely — goes straight to the Discogs/Spotify/
    YouTube/Bandcamp enrichment pipeline. Useful when a user already knows
    the album (e.g. read it off a sleeve text-only) and just wants the
    cross-platform links and tracklist.
    """
    # Lookup doesn't need Claude — it's pure metadata enrichment. So the
    # CLAUDE_CODE_OAUTH_TOKEN check from /identify doesn't apply here.
    ser = ManualLookupSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    artist = ser.validated_data['artist'].strip()
    album = ser.validated_data['album'].strip()

    searcher = _hybrid_searcher()
    if searcher is None:
        return Response(
            {'error': 'hybrid search unavailable', 'detail': 'See server logs.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    started = time.time()
    try:
        result = asyncio.run(searcher.manual_lookup(artist, album))
    except Exception as e:
        logger.error("lookup failed for %r/%r: %s", artist, album, e, exc_info=True)
        return Response(
            {'error': 'lookup failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    duration_ms = int((time.time() - started) * 1000)

    # Persist as a manual identification so audit log + recent-results list
    # surface it alongside image-driven IDs.
    flat = _flatten_search_result(result) if isinstance(result, dict) else {}
    identification = AlbumIdentification.objects.create(
        image_hash='',
        method='manual',
        raw_response=result if isinstance(result, dict) else {},
        artist_guess=flat.get('artist') or artist,
        album_guess=flat.get('album') or album,
        confidence=flat.get('confidence_numeric'),
        error_message=(result or {}).get('error', '') if isinstance(result, dict) else '',
    )
    release = None
    if isinstance(result, dict) and not result.get('error') and (flat.get('artist') or flat.get('album')):
        release = IdentifiedRelease.objects.create(
            identification=identification,
            artist=flat.get('artist') or artist,
            album=flat.get('album') or album,
            release_date=flat.get('release_date') or '',
            genres=flat.get('genres') or [],
            cover_image_url=flat.get('cover_image_url') or '',
            discogs_url=flat.get('discogs_url') or '',
            spotify_url=flat.get('spotify_url') or '',
            youtube_url=flat.get('youtube_url') or '',
            bandcamp_url=flat.get('bandcamp_url') or '',
            tracklist=flat.get('tracklist') or [],
            market_stats=flat.get('market_stats') or {},
            extra=flat.get('extra') or {},
        )

    payload = dict(result) if isinstance(result, dict) else {'error': 'unexpected result'}
    payload['cratemate'] = {
        'identification_id': identification.id,
        'release_id': release.id if release else None,
        'duration_ms': duration_ms,
        'method': 'manual',
    }
    return Response(payload, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_DEBUG_IMAGE_DIR = '/tmp/cratemate-debug'
_DEBUG_IMAGE_KEEP = 50


def _stash_debug_image(raw_bytes: bytes, image_hash: str) -> None:
    """Save the upload to a bounded debug directory.

    Filenames look like `2026-05-10T22-34-12_<hash>.jpg`. Cleanup keeps only
    the newest _DEBUG_IMAGE_KEEP files so production disk doesn't fill from a
    burst of uploads. Failures here are silent — debug aid only.
    """
    import os
    import time as _time
    try:
        os.makedirs(_DEBUG_IMAGE_DIR, exist_ok=True)
        ts = _time.strftime('%Y-%m-%dT%H-%M-%S')
        path = os.path.join(_DEBUG_IMAGE_DIR, f'{ts}_{image_hash[:12]}.jpg')
        with open(path, 'wb') as f:
            f.write(raw_bytes)
        # Trim oldest if over the cap.
        files = sorted(
            (os.path.join(_DEBUG_IMAGE_DIR, n) for n in os.listdir(_DEBUG_IMAGE_DIR)),
            key=lambda p: os.path.getmtime(p),
        )
        for old in files[:-_DEBUG_IMAGE_KEEP]:
            try:
                os.remove(old)
            except OSError:
                pass
    except Exception as e:
        logger.debug('debug image stash failed: %s', e)


# Map "high|medium|low" → 0–1 numeric for the AlbumIdentification.confidence
# float column. Mirrors the buckets gemini.py / claude_vision.py already emit.
_CONFIDENCE_BUCKETS = {'high': 0.9, 'medium': 0.7, 'low': 0.5}


def _flatten_search_result(result: dict) -> dict:
    """Normalize hybrid_search's nested response into the flat shape the
    AlbumIdentification + IdentifiedRelease tables expect.

    hybrid_search returns:
        {
          "album": {"name", "artist", "release_date", "genres", "image", ...},
          "links": {"discogs", "spotify", "youtube", "bandcamp"},
          "tracks": {"tracklist", "spotify_tracks", "youtube_tracks", ...},
          "market_stats": {...},
          "release_overview": {...},
          "identification": {"confidence": 0.0–1.0, "method", "source"},
        }

    Older Gemini-era responses (pre-2026-05) used flat top-level keys like
    `artist_name`, `album_name`, `discogs_url`. We accept both so DB rows
    don't lose data depending on what shape leaked through.
    """
    album = result.get('album') if isinstance(result.get('album'), dict) else {}
    links = result.get('links') if isinstance(result.get('links'), dict) else {}
    tracks = result.get('tracks') if isinstance(result.get('tracks'), dict) else {}
    ident = result.get('identification') if isinstance(result.get('identification'), dict) else {}

    artist = album.get('artist') or result.get('artist_name') or ''
    album_name = album.get('name') or result.get('album_name') or ''

    # Tracklist: prefer the rich youtube_tracks (has YouTube mapping) → spotify_tracks → raw tracklist
    tracklist = (
        tracks.get('youtube_tracks')
        or tracks.get('spotify_tracks')
        or tracks.get('tracklist')
        or []
    )

    # Confidence: hybrid_search puts a float in identification.confidence;
    # claude_vision/gemini emit a string bucket. Normalize to float.
    raw_conf = ident.get('confidence')
    if isinstance(raw_conf, (int, float)):
        confidence_numeric = float(raw_conf)
    elif isinstance(raw_conf, str):
        confidence_numeric = _CONFIDENCE_BUCKETS.get(raw_conf.lower())
    else:
        confidence_numeric = None

    flat = {
        'artist': artist,
        'album': album_name,
        'release_date': album.get('release_date') or result.get('release_date') or '',
        'genres': album.get('genres') or result.get('genres') or [],
        'cover_image_url': album.get('image') or result.get('album_image') or '',
        'discogs_url': links.get('discogs') or result.get('discogs_url') or '',
        'spotify_url': links.get('spotify') or result.get('spotify_url') or '',
        'youtube_url': links.get('youtube') or result.get('youtube_url') or '',
        'bandcamp_url': links.get('bandcamp') or result.get('bandcamp_url') or '',
        'tracklist': tracklist,
        'market_stats': result.get('market_stats') or {},
        'confidence_numeric': confidence_numeric,
    }
    # Anything we didn't explicitly extract goes into extra so we don't lose it.
    consumed = {
        'album', 'links', 'tracks', 'identification',
        'artist_name', 'album_name', 'release_date', 'genres',
        'album_image', 'discogs_url', 'spotify_url', 'youtube_url',
        'bandcamp_url', 'market_stats', 'cratemate',
    }
    flat['extra'] = {k: v for k, v in result.items() if k not in consumed}
    return flat


@api_view(['GET'])
def result_list(request):
    """List recent album identifications."""
    limit = int(request.query_params.get('limit', 50))
    qs = AlbumIdentification.objects.all()[:limit]
    serializer = AlbumIdentificationSerializer(qs, many=True)
    return Response({'results': serializer.data})


@api_view(['GET'])
def result_detail(request, pk):
    try:
        ident = AlbumIdentification.objects.get(pk=pk)
    except AlbumIdentification.DoesNotExist:
        return Response({'error': 'not found'}, status=status.HTTP_404_NOT_FOUND)
    serializer = AlbumIdentificationSerializer(ident)
    return Response(serializer.data)


@api_view(['GET'])
def run_list(request):
    """Audit log — recent recognition runs."""
    limit = int(request.query_params.get('limit', 50))
    qs = RecognitionRun.objects.all()[:limit]
    serializer = RecognitionRunSerializer(qs, many=True)
    return Response({'results': serializer.data})
