import logging

from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from django.db.models import Count
from django.conf import settings
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from .models import WantedSource, WantedItem, ImportOperation
from .serializers import (
    WantedSourceSerializer, WantedItemSerializer, BulkAddSerializer,
    ImportOperationSerializer, ImportOperationListSerializer,
    TriggerImportSerializer, ConfirmImportSerializer,
)
from .services.preview import find_preview
from .services.shazam import ingest as ingest_shazam, sync_from_spotify
from drain.auth import require_drain_token
from .services import (
    run_youtube_import, run_soundcloud_import,
    run_spotify_import, get_spotify_auth_url, handle_spotify_callback, check_spotify_status,
    run_discogs_import, run_bandcamp_import,
)

logger = logging.getLogger(__name__)


class WantedSourceViewSet(viewsets.ModelViewSet):
    serializer_class = WantedSourceSerializer
    filterset_fields = ['source_type', 'active']
    search_fields = ['name']

    def get_queryset(self):
        return WantedSource.objects.annotate(
            item_count=Count('items')
        )


class WantedItemViewSet(viewsets.ModelViewSet):
    serializer_class = WantedItemSerializer

    def get_queryset(self):
        return WantedItem.objects.select_related('source').annotate(
            search_results_count=Count('search_results')
        )
    filterset_fields = ['status', 'source', 'identified_via']
    search_fields = ['artist', 'title', 'notes']
    ordering_fields = ['added', 'updated', 'artist', 'title', 'status']

    @action(detail=True, methods=['post'])
    def preview(self, request, pk=None):
        """Resolve (and cache) a 30s preview for this track.

        On demand rather than at ingest: most rows are never played, and
        resolving every one would mean two third-party lookups per track for
        no reason. `refresh=true` re-asks a track previously found to have
        none — worth it after a promo finally gets a proper release.
        """
        item = self.get_object()
        refresh = str(request.data.get('refresh', '')).lower() in ('1', 'true', 'yes')

        if item.preview_checked and not refresh:
            return Response({'url': item.preview_url,
                             'provider': item.preview_provider,
                             'cached': True})

        url, provider = find_preview(item.artist, item.title)
        item.preview_url = url
        item.preview_provider = provider
        item.preview_checked = timezone.now()
        item.save(update_fields=['preview_url', 'preview_provider', 'preview_checked'])
        return Response({'url': url, 'provider': provider, 'cached': False})

    @action(detail=False, methods=['post'])
    def bulk_add(self, request):
        """Add multiple wanted items at once."""
        serializer = BulkAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        source_id = serializer.validated_data.get('source_id')
        items_data = serializer.validated_data['items']

        created = []
        for item_data in items_data:
            item = WantedItem.objects.create(
                artist=item_data.get('artist', ''),
                title=item_data.get('title', ''),
                release_name=item_data.get('release_name', ''),
                catalog_number=item_data.get('catalog_number', ''),
                label=item_data.get('label', ''),
                notes=item_data.get('notes', ''),
                source_id=source_id,
            )
            created.append(item)

        return Response(
            WantedItemSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['post'])
    def bulk_update_status(self, request):
        """Update status for multiple items."""
        ids = request.data.get('ids', [])
        new_status = request.data.get('status')

        if not ids or not new_status:
            return Response(
                {'error': 'ids and status are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        valid_statuses = [c[0] for c in WantedItem.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {'error': f'Invalid status. Must be one of: {valid_statuses}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = WantedItem.objects.filter(id__in=ids).update(status=new_status)
        return Response({'updated': updated})

    @action(detail=False, methods=['delete'])
    def bulk_delete(self, request):
        """Delete multiple items."""
        ids = request.data.get('ids', [])
        if not ids:
            return Response(
                {'error': 'ids are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deleted, _ = WantedItem.objects.filter(id__in=ids).delete()
        return Response({'deleted': deleted})


# ── Import Operations ──────────────────────────────────────────

@api_view(['GET'])
def import_operations(request):
    """List all import operations."""
    qs = ImportOperation.objects.select_related('source').all()

    import_type = request.query_params.get('import_type')
    if import_type:
        qs = qs.filter(import_type=import_type)

    op_status = request.query_params.get('status')
    if op_status:
        qs = qs.filter(status=op_status)

    limit = int(request.query_params.get('limit', 50))
    qs = qs[:limit]

    serializer = ImportOperationListSerializer(qs, many=True)
    return Response({'results': serializer.data})


@api_view(['GET'])
def import_operation_detail(request, pk):
    """Get a single import operation with preview data."""
    try:
        op = ImportOperation.objects.select_related('source').get(pk=pk)
    except ImportOperation.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = ImportOperationSerializer(op)
    return Response(serializer.data)


@api_view(['POST'])
def trigger_import(request):
    """Trigger a new import operation."""
    ser = TriggerImportSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    import_type = ser.validated_data['import_type']
    url = ser.validated_data.get('url', '')

    # Reuse a single source per import type
    source_name = f"{import_type.capitalize()} Import"
    source, _ = WantedSource.objects.get_or_create(
        source_type=import_type,
        defaults={'name': source_name, 'url': url},
    )
    if url and source.url != url:
        source.url = url
        source.save(update_fields=['url'])

    op = ImportOperation.objects.create(
        import_type=import_type,
        url=url,
        source=source,
    )

    # Dispatch to the right service
    runners = {
        'youtube': run_youtube_import,
        'soundcloud': run_soundcloud_import,
        'spotify': run_spotify_import,
        'discogs': run_discogs_import,
        'bandcamp': run_bandcamp_import,
    }
    runner = runners[import_type]
    runner(op.id)

    serializer = ImportOperationSerializer(op)
    return Response(serializer.data, status=status.HTTP_202_ACCEPTED)


@api_view(['POST'])
def confirm_import(request, pk):
    """Confirm and create WantedItems from selected preview items."""
    try:
        op = ImportOperation.objects.get(pk=pk)
    except ImportOperation.DoesNotExist:
        return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

    if op.status != 'previewing':
        return Response(
            {'error': f'Operation is not in previewing state (status: {op.status})'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    ser = ConfirmImportSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    selected_indices = ser.validated_data['items']

    op.status = 'importing'
    op.save()

    created_count = 0
    skipped = 0
    errors = []

    for idx in selected_indices:
        if idx < 0 or idx >= len(op.preview_data):
            skipped += 1
            continue

        track = op.preview_data[idx]
        try:
            WantedItem.objects.create(
                artist=track.get('artist', ''),
                title=track.get('title', ''),
                release_name=track.get('release_name', ''),
                catalog_number=track.get('catalog_number', ''),
                label=track.get('label', ''),
                source=op.source,
                notes=track.get('source_url', ''),
            )
            created_count += 1
        except Exception as e:
            errors.append(str(e))

    op.items_imported = created_count
    op.summary = {
        'items_imported': created_count,
        'items_skipped': skipped,
        'errors': errors,
    }
    op.status = 'completed'
    op.save()

    return Response(ImportOperationSerializer(op).data)


@api_view(['GET'])
def spotify_auth_url(request):
    """Return the Spotify OAuth authorization URL."""
    try:
        url = get_spotify_auth_url()
        return Response({'url': url})
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['GET'])
def spotify_callback(request):
    """Handle Spotify OAuth callback."""
    code = request.query_params.get('code')
    if not code:
        return Response(
            {'error': 'Missing authorization code'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        handle_spotify_callback(code)
        # Redirect to frontend
        frontend_url = request.query_params.get('state', 'http://localhost:5174')
        from django.shortcuts import redirect
        return redirect(f'{frontend_url}?spotify=connected')
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['GET'])
def spotify_status(request):
    """Check if Spotify is connected."""
    return Response(check_spotify_status())


@api_view(['GET'])
def import_config_status(request):
    """Return which import sources are configured and available."""
    from core.views import get_config

    spotify_cfg = check_spotify_status()
    discogs_token = get_config('DISCOGS_PERSONAL_TOKEN')
    discogs_user = get_config('DISCOGS_USERNAME')
    yt_api_key = get_config('YOUTUBE_API_KEY')
    sc_client_id = get_config('SC_CLIENT_ID')

    return Response({
        'youtube': {
            'available': True,
            'api_configured': bool(yt_api_key),
        },
        'soundcloud': {
            'available': True,
            'api_configured': bool(sc_client_id),
        },
        'spotify': {
            'available': bool(spotify_cfg.get('configured')),
            'connected': bool(spotify_cfg.get('connected')),
        },
        'discogs': {
            'available': bool(discogs_token and discogs_user),
        },
        'bandcamp': {
            'available': True,
        },
    })


# ── Shazam ingest ─────────────────────────────────────────────

@api_view(['POST'])
@require_drain_token
def shazam_ingest(request):
    """Take a batch of Shazams and turn them into WantedItems.

    Kept alongside the Spotify poller so a feed that isn't Spotify — anything
    that can hold a bearer token and speak JSON — can still push here without
    touching the rest of the app.

    An empty batch is the normal case, not an error: a caller may post every
    cycle whether or not it found anything, so `last_checked` means "the feed
    is alive" rather than "something was Shazamed recently".
    """
    items = request.data.get('items')
    if not isinstance(items, list):
        return Response({'error': 'items must be a list'},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(ingest_shazam(items))


@api_view(['POST'])
def shazam_sync_now(request):
    """Poll Spotify immediately instead of waiting for the next cycle.

    `seed=true` advances the cursor past everything currently in the playlist
    without importing it — connecting Shazam to Spotify backfills the whole
    library, and this feed is meant to start from now.
    """
    seed = str(request.data.get('seed', '')).lower() in ('1', 'true', 'yes')
    try:
        return Response(sync_from_spotify(seed=seed))
    except Exception as e:
        logger.exception('shazam manual sync failed')
        return Response({'error': repr(e)}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(['GET'])
def shazam_status(request):
    """What the Shazam panel needs to say whether the feed is working.

    `last_checked` is a heartbeat from the Mac reader, not the time of the last
    Shazam — the reader posts every cycle even with nothing to send.
    """
    from .services.spotify import check_spotify_status

    source = WantedSource.objects.filter(source_type='shazam').first()
    qs = WantedItem.objects.filter(identified_via='shazam')

    last_seen = source.last_checked if source else None
    # The poller runs every 10 minutes. Three missed cycles is not jitter —
    # the worker is down or Spotify's refresh token has expired, and the
    # latter fails silently: nothing arrives and nothing complains.
    overdue = True
    if last_seen:
        overdue = (timezone.now() - last_seen).total_seconds() > 1800

    try:
        spotify = check_spotify_status()
    except Exception:
        spotify = {'configured': False, 'connected': False}

    return Response({
        'spotify_connected': bool(spotify.get('connected')),
        'seeded': bool(source and source.cursor),
        'last_checked': last_seen,
        'overdue': overdue,
        'total': qs.count(),
        'by_status': {
            row['status']: row['n']
            for row in qs.values('status').annotate(n=Count('id')).order_by()
        },
    })
