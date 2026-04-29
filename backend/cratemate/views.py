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
    """Lazy import — heavy graph (PIL + google-generativeai + fuzzywuzzy)
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
    booting the heavy hybrid searcher when nothing's set."""
    import os
    key = os.getenv('CRATEMATE_GEMINI_API_KEY', '')
    return bool(key) and key != '__PENDING__'


@api_view(['GET'])
def status_view(request):
    """Operational status of the cratemate module."""
    import os
    return Response({
        'status': 'operational' if _credentials_configured() else 'unconfigured',
        'features': {
            'gemini_vision': bool(os.getenv('CRATEMATE_GEMINI_API_KEY', '')) and os.getenv('CRATEMATE_GEMINI_API_KEY') != '__PENDING__',
            'discogs': bool(os.getenv('CRATEMATE_DISCOGS_TOKEN', '')) and os.getenv('CRATEMATE_DISCOGS_TOKEN') != '__PENDING__',
            'spotify': bool(os.getenv('CRATEMATE_SPOTIFY_CLIENT_ID', '')) and os.getenv('CRATEMATE_SPOTIFY_CLIENT_ID') != '__PENDING__',
            'youtube': bool(os.getenv('CRATEMATE_YOUTUBE_API_KEY') or os.getenv('YOUTUBE_API_KEY')),
            'gcp_vision': bool(os.getenv('CRATEMATE_GCP_SA_JSON', '')) and os.getenv('CRATEMATE_GCP_SA_JSON') != '__PENDING__',
        },
        'description': 'Album-cover identification + multi-platform metadata enrichment.',
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
                'detail': 'Set CRATEMATE_GEMINI_API_KEY (and friends) in '
                          '~/.secrets/ocdj-cratemate.env or Dokploy env, then restart.',
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
    identification = AlbumIdentification.objects.create(
        image_hash=image_hash,
        method='gemini',
        raw_response=result if isinstance(result, dict) else {},
        artist_guess=(result or {}).get('artist_name', '') or (result or {}).get('album', {}).get('artist', '') if isinstance(result, dict) else '',
        album_guess=(result or {}).get('album_name', '') or (result or {}).get('album', {}).get('name', '') if isinstance(result, dict) else '',
        confidence=(result or {}).get('confidence') if isinstance(result, dict) else None,
        error_message=(result or {}).get('error', '') if isinstance(result, dict) else '',
    )

    release = None
    if isinstance(result, dict) and not result.get('error'):
        release = IdentifiedRelease.objects.create(
            identification=identification,
            artist=identification.artist_guess or '',
            album=identification.album_guess or '',
            release_date=result.get('release_date') or '',
            genres=result.get('genres') or [],
            cover_image_url=result.get('album_image') or '',
            discogs_url=result.get('discogs_url') or '',
            spotify_url=result.get('spotify_url') or '',
            youtube_url=result.get('youtube_url') or '',
            bandcamp_url=result.get('bandcamp_url') or '',
            tracklist=result.get('tracks', {}).get('tracklist', []) if isinstance(result.get('tracks'), dict) else [],
            market_stats=result.get('market_stats') or {},
            extra={k: v for k, v in result.items() if k not in (
                'artist_name', 'album_name', 'release_date', 'genres',
                'album_image', 'discogs_url', 'spotify_url', 'youtube_url',
                'bandcamp_url', 'tracks', 'market_stats',
            )},
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
    """Manual artist+album fallback when image identification isn't useful."""
    if not _credentials_configured():
        return Response(
            {'error': 'cratemate credentials not configured'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    ser = ManualLookupSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    return Response(
        {'error': 'manual lookup not yet implemented in V1', 'detail': ser.validated_data},
        status=status.HTTP_501_NOT_IMPLEMENTED,
    )


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
