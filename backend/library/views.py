import threading

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q

from .models import LibraryTrack
from .serializers import LibraryTrackSerializer


@api_view(['GET'])
def track_list(request):
    """List library tracks with filtering, search, and pagination."""
    tracks = LibraryTrack.objects.all()

    # Filters
    fmt = request.query_params.get('format')
    if fmt:
        tracks = tracks.filter(format=fmt)

    genre = request.query_params.get('genre')
    if genre:
        tracks = tracks.filter(genre__icontains=genre)

    artist = request.query_params.get('artist')
    if artist:
        tracks = tracks.filter(artist__icontains=artist)

    label = request.query_params.get('label')
    if label:
        tracks = tracks.filter(label__icontains=label)

    missing = request.query_params.get('missing')
    if missing == 'true':
        tracks = tracks.filter(missing=True)
    elif missing == 'false' or missing is None:
        tracks = tracks.filter(missing=False)

    # Search across artist, title, album, label
    search = request.query_params.get('search')
    if search:
        tracks = tracks.filter(
            Q(artist__icontains=search) |
            Q(title__icontains=search) |
            Q(album__icontains=search) |
            Q(label__icontains=search) |
            Q(catalog_number__icontains=search)
        )

    # Ordering
    ordering = request.query_params.get('ordering', '-added_at')
    allowed_ordering = [
        'artist', '-artist', 'title', '-title', 'added_at', '-added_at',
        'duration_seconds', '-duration_seconds', 'file_size_bytes', '-file_size_bytes',
    ]
    if ordering in allowed_ordering:
        tracks = tracks.order_by(ordering)

    paginator = PageNumberPagination()
    paginator.page_size = 50
    page = paginator.paginate_queryset(tracks, request)
    serializer = LibraryTrackSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(['GET'])
def track_detail(request, pk):
    """Get a single track's details."""
    try:
        track = LibraryTrack.objects.get(pk=pk)
    except LibraryTrack.DoesNotExist:
        return Response({'error': 'Not found'}, status=http_status.HTTP_404_NOT_FOUND)

    return Response(LibraryTrackSerializer(track).data)


@api_view(['PATCH'])
def track_update(request, pk):
    """Update track metadata (writes to file too)."""
    try:
        track = LibraryTrack.objects.get(pk=pk)
    except LibraryTrack.DoesNotExist:
        return Response({'error': 'Not found'}, status=http_status.HTTP_404_NOT_FOUND)

    editable = ['artist', 'title', 'album', 'label', 'catalog_number', 'genre', 'year']
    data = {k: v for k, v in request.data.items() if k in editable}

    if not data:
        return Response({'error': 'No valid fields provided'}, status=http_status.HTTP_400_BAD_REQUEST)

    try:
        from .services import update_track_metadata
        track = update_track_metadata(track, data)
        return Response(LibraryTrackSerializer(track).data)
    except Exception as e:
        return Response({'error': str(e)}, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def scan_library(request):
    """Trigger a library scan in background."""
    from .services import scan_library as do_scan
    from django import db

    def _scan():
        try:
            result = do_scan()
            # Store result in a simple way - log it
            import logging
            logging.getLogger(__name__).info(f"Library scan complete: {result}")
        finally:
            db.connections.close_all()

    threading.Thread(target=_scan, daemon=True).start()
    return Response({'message': 'Library scan started'})


@api_view(['POST'])
def scan_library_sync(request):
    """Trigger a library scan synchronously (for smaller libraries)."""
    from .services import scan_library as do_scan
    result = do_scan()
    return Response(result)


@api_view(['GET'])
def library_stats(request):
    """Library statistics."""
    from .services import get_library_stats
    return Response(get_library_stats())
