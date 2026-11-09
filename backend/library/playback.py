"""Browser routes retain protected ingress. Connector uses existing bearer + SSH."""
import os
import secrets
from datetime import timedelta

from django.db import transaction
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from drain.auth import require_drain_token
from . import playback_cache as cache
from .matching import MAC_KEY
from .models import LibraryTrack, MacPlayback

REQUEST_SECONDS = 180


def eligible(track):
    return track.source == 'mac_snapshot' and not track.missing and MAC_KEY.fullmatch(track.file_path)


def state(job):
    now = timezone.now()
    if not eligible(job.track):
        return 'unavailable'
    if job.cache_key and job.cache_until and job.cache_until > now and cache.path(job.cache_key).is_file():
        return 'ready'
    if job.failed:
        return 'unavailable'
    return 'pending' if job.requested_until and job.requested_until > now else 'offline'


@api_view(['GET', 'POST'])
def prepare(request, pk):
    track = get_object_or_404(LibraryTrack, pk=pk)
    if not eligible(track):
        return Response({'status': 'unavailable'}, status=409)
    if request.method == 'POST':
        with transaction.atomic():
            # Serialize creation and refresh against concurrent requests for this track.
            track = LibraryTrack.objects.select_for_update().get(pk=pk)
            job, _ = MacPlayback.objects.get_or_create(track=track)
            if state(job) not in ('ready', 'pending'):
                job.requested_until = timezone.now() + timedelta(seconds=REQUEST_SECONDS)
                job.lease_token = ''; job.lease_until = None; job.failed = False
                job.cache_until = None
                job.save()
    else:
        job = MacPlayback.objects.filter(track=track).first()
    status = state(job) if job else 'offline'
    data = {'status': status}
    if status == 'ready':
        data['url'] = f'/api/library/tracks/{pk}/audio/'
        data['expires_at'] = job.cache_until
    response = Response(data)
    response['Cache-Control'] = 'private, no-store'
    return response


@api_view(['POST'])
@require_drain_token
def claim(request):
    collection = request.data.get('collection_id', '')
    # Exact collection match, not a client supplied path, URL, or shell expression.
    from uuid import UUID
    try:
        if str(UUID(collection)) != collection:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        return Response({'error': 'Invalid collection'}, status=400)
    with transaction.atomic():
        now = timezone.now()
        jobs = MacPlayback.objects.select_for_update(skip_locked=True).filter(
            requested_until__gt=now, failed=False, track__source='mac_snapshot',
            track__missing=False, track__file_path__startswith=f'mac-snapshot:{collection}:',
        ).exclude(cache_until__gt=now).exclude(lease_until__gt=now).order_by('requested_until')
        job = jobs.first()
        if not job or not eligible(job.track):
            return Response(status=204)
        job.lease_token = secrets.token_hex(24)
        job.lease_until = job.requested_until
        job.save()
        key = MAC_KEY.fullmatch(job.track.file_path)
        return Response(dict(track_id=job.pk, collection_id=key[1], opaque_id=key[2],
                             token=job.lease_token))


def leased(job, token):
    return (eligible(job.track) and job.lease_until and job.lease_until > timezone.now()
            and job.lease_token and isinstance(token, str)
            and secrets.compare_digest(job.lease_token.encode(), token.encode()))


@api_view(['POST'])
@require_drain_token
def complete(request, pk):
    token = request.headers.get('X-Playback-Lease', '')
    job = get_object_or_404(MacPlayback, pk=pk)
    if not leased(job, token):
        return Response({'error': 'Stale lease'}, status=409)
    # Failure carries no filenames, diagnostics or arbitrary client messages.
    if request.content_type == 'application/json':
        with transaction.atomic():
            job = MacPlayback.objects.select_for_update().get(pk=pk)
            if not leased(job, token):
                return Response({'error': 'Stale lease'}, status=409)
            job.failed = True; job.lease_token = ''; job.lease_until = None; job.save()
        return Response({'status': 'unavailable'})
    if request.content_type != 'audio/mpeg':
        return Response({'error': 'Expected MP3'}, status=400)
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
        if not 12 <= length <= cache.MAX_BYTES:
            return Response({'error': 'Invalid audio size'}, status=413)
        with cache.staged(request) as key:
            with transaction.atomic():
                job = MacPlayback.objects.select_for_update().get(pk=pk)
                if not leased(job, token):
                    raise ValueError('Stale lease')
                previous = job.cache_key
                job.cache_key = key
                job.cache_until = timezone.now() + timedelta(seconds=cache.TTL_SECONDS)
                job.requested_until = None
                job.lease_token = ''; job.lease_until = None; job.save()
            if previous and previous != key:
                cache.path(previous).unlink(missing_ok=True)
    except Exception:
        # Parser/filesystem exceptions can contain private input. Do not echo them.
        return Response({'error': 'Audio staging refused'}, status=409)
    return Response({'status': 'ready'})


@never_cache
@api_view(['GET', 'HEAD'])
def audio(request, pk):
    job = get_object_or_404(MacPlayback, pk=pk)
    if state(job) != 'ready':
        return Response({'status': state(job)}, status=410)
    try:
        handle = cache.path(job.cache_key).open('rb')
    except FileNotFoundError:
        return Response(status=410)
    size = os.fstat(handle.fileno()).st_size
    try:
        start, end, code = cache.byte_range(request.headers.get('Range'), size)
    except ValueError:
        handle.close()
        return HttpResponse(status=416, headers={'Content-Range': f'bytes */{size}'})

    def chunks():
        try:
            handle.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            handle.close()
    if request.method == 'HEAD':
        handle.close()
        response = HttpResponse(status=code, content_type='audio/mpeg')
    else:
        response = StreamingHttpResponse(chunks(), status=code, content_type='audio/mpeg')
    response['Content-Length'] = end - start + 1
    response['Accept-Ranges'] = 'bytes'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    if code == 206:
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
    return response
