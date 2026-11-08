import re
import os
import secrets
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.http import StreamingHttpResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from drain.auth import require_drain_token
from wanted.models import WantedItem
from wanted.services.preview import find_exact_preview
from .models import Collection, Post, Media
from .serializers import COLLECTION_ID, Candidate, SyncInput, EditInput, Completion
from . import cache
from .recognition import explicit_candidates


def collection():
    return Collection.objects.get_or_create(instagram_id=COLLECTION_ID)[0]


def wanted_matches_current(m):
    return bool(m.wanted_id and m.artist and m.title
                and m.wanted.artist.casefold() == m.artist.casefold()
                and m.wanted.title.casefold() == m.title.casefold())


def child_json(m):
    ready = bool(m.cache_key and m.cache_until and m.cache_until > timezone.now())
    return dict(id=m.pk, instagram_id=m.instagram_id, position=m.position, kind=m.kind,
                status=m.status, candidates=m.candidates, artist=m.artist, title=m.title,
                recognition=m.recognition, playback_requested=m.playback_requested, playback_error=m.playback_error,
                reviewed=m.reviewed, wanted_id=m.wanted_id,
                wanted_matches_current=wanted_matches_current(m), queued=m.queued,
                playback_version=m.cache_until.isoformat() if ready else None,
                playback_url=f'/api/instagram/media/{m.pk}/stream/' if ready else None)


@api_view(['GET'])
def workspace(request):
    c = collection()
    posts = c.posts.filter(active=True).prefetch_related('media__wanted')
    now = timezone.now()
    online = c.heartbeat_at and now - c.heartbeat_at < timedelta(minutes=3)
    return Response(dict(name=c.name, fixture=c.fixture, synced_at=c.synced_at,
                         connector_status=c.connector_status if online else 'offline',
                         posts=[dict(id=p.pk, author=p.author, caption=p.caption, permalink=p.permalink,
                                     media=[child_json(m) for m in p.media.all() if m.active]) for p in posts]))


@api_view(['POST'])
def candidate_preview(request, pk):
    media = get_object_or_404(Media, pk=pk, active=True, post__active=True, kind='video')
    candidate = Candidate(data=request.data)
    candidate.is_valid(raise_exception=True)
    if candidate.validated_data not in media.candidates:
        return Response(status=404)

    url, provider, track_url = find_exact_preview(**candidate.validated_data)
    return Response({'url': url, 'provider': provider, 'track_url': track_url},
                    headers={'Cache-Control': 'private, no-store'})


@api_view(['PATCH', 'POST'])
def media_action(request, pk, action='edit'):
    with transaction.atomic():
        m = get_object_or_404(Media.objects.select_for_update(), pk=pk, active=True, post__active=True)
        if action == 'edit' and request.method == 'PATCH':
            s = EditInput(data=request.data); s.is_valid(raise_exception=True)
            for k, v in s.validated_data.items():
                setattr(m, k, v)
            # A correction fences any result already in flight.
            if 'artist' in s.validated_data or 'title' in s.validated_data:
                m.lease_token = ''; m.lease_until = None; m.queued = False
                m.status = 'identified' if m.artist and m.title else 'pending'
        elif action == 'recognize' and request.method == 'POST':
            if m.kind != 'video':
                raise ValidationError('Only videos can be recognized')
            if not m.queued:
                candidates, records = explicit_candidates(m.post.caption, m.position)
                if candidates:
                    m.candidates = candidates; m.status = 'identified'
                    m.recognition = dict(source='caption', certainty='explicit', outcome='identified',
                                         comments='not-read', records=records)
                    if not m.reviewed and len(candidates) == 1:
                        m.artist = candidates[0]['artist']; m.title = candidates[0]['title']
                else:
                    m.queued = True; m.status = 'pending'; m.recognition = {}
                    if m.cache_key and m.cache_until and m.cache_until > timezone.now():
                        from .tasks import recognize_cached
                        transaction.on_commit(lambda: recognize_cached(m.pk))
        elif action == 'playback' and request.method == 'POST':
            # Explicit retries must also refresh a missing/corrupt cached object.
            m.playback_requested = True; m.playback_error = False
        elif action == 'wishlist' and request.method == 'POST':
            if not m.artist or not m.title:
                raise ValidationError('Choose or correct artist and title first')
            if not wanted_matches_current(m):
                # Relink corrections without editing the previous (possibly shared) item.
                # Media lock deduplicates retries; also reuse a previously wanted track.
                item = WantedItem.objects.filter(artist__iexact=m.artist, title__iexact=m.title).first()
                m.wanted = item or WantedItem.objects.create(artist=m.artist, title=m.title,
                    status='identified', identified_via='manual', notes=m.post.permalink)
        else:
            return Response(status=405)
        m.save()
    return Response(child_json(m))


@api_view(['POST'])
@require_drain_token
def heartbeat(request):
    status = request.data.get('status')
    if status not in ['ready', 'session-expired', 'restricted', 'rate-limited', 'error']:
        raise ValidationError('Invalid connector status')
    c = collection(); c.heartbeat_at = timezone.now(); c.connector_status = status; c.save()
    cache.cleanup()
    return Response({'ok': True})


@api_view(['POST'])
@require_drain_token
def sync(request):
    s = SyncInput(data=request.data); s.is_valid(raise_exception=True)
    data = s.validated_data
    with transaction.atomic():
        c = collection()
        c = Collection.objects.select_for_update().get(pk=c.pk)
        # Refuse ID re-parenting before applying any changes.
        for p in data['posts']:
            for ch in p['children']:
                if Media.objects.filter(instagram_id=ch['id']).exclude(post__instagram_id=p['id']).exists():
                    raise ValidationError('Media belongs to another post')
        c.posts.update(active=False)
        for rank, p in enumerate(data['posts']):
            post, _ = Post.objects.update_or_create(instagram_id=p['id'], defaults=dict(
                collection=c, rank=rank, active=True, permalink=p['permalink'], author=p['author'], caption=p['caption']))
            post.media.update(active=False)
            for pos, ch in enumerate(p['children']):
                Media.objects.update_or_create(instagram_id=ch['id'], defaults=dict(post=post,
                    position=pos, kind=ch['kind'], active=True))
        c.synced_at = timezone.now(); c.fixture = data['fixture']; c.save()
    cache.cleanup()
    return Response({'posts': len(data['posts']), 'media': list(Media.objects.filter(active=True, post__active=True).values('id', 'instagram_id', 'kind'))})


@api_view(['POST'])
@require_drain_token
def claim(request):
    with transaction.atomic():
        now = timezone.now()
        m = Media.objects.select_for_update(skip_locked=True).filter(Q(queued=True) | Q(playback_requested=True), active=True,
            post__active=True).exclude(lease_until__gt=now).order_by('post__rank', 'position').first()
        if not m:
            return Response(status=204)
        m.lease_action = 'recognize' if m.queued else 'playback'
        m.lease_token = secrets.token_hex(24); m.lease_until = now + timedelta(minutes=10)
        if m.queued: m.status = 'running'
        m.save()
        return Response(dict(id=m.pk, instagram_id=m.instagram_id, token=m.lease_token,
                             permalink=m.post.permalink, post_instagram_id=m.post.instagram_id, position=m.position,
                             action=m.lease_action, mime='video/mp4' if m.kind == 'video' else 'image/jpeg', lease_until=m.lease_until))


@api_view(['POST'])
@require_drain_token
def complete(request, pk):
    s = Completion(data=request.data); s.is_valid(raise_exception=True)
    d = s.validated_data
    with transaction.atomic():
        m = get_object_or_404(Media.objects.select_for_update(), pk=pk)
        if not (m.queued or m.playback_requested) or not m.lease_token or not secrets.compare_digest(m.lease_token, d['token']) or m.lease_until <= timezone.now():
            return Response({'error': 'stale lease'}, status=409)
        if m.lease_action != 'playback' and m.queued:
            m.status = d['status']; m.candidates = d['candidates']; m.queued = False
            m.recognition = d.get('recognition', dict(source='shazam', certainty='tentative',
                                 outcome=d['status'], comments='not-read'))
            if m.candidates and not m.reviewed and len(m.candidates) == 1:
                m.artist = m.candidates[0]['artist']; m.title = m.candidates[0]['title']
        if m.lease_action == 'playback' or (m.cache_key and m.cache_until and m.cache_until > timezone.now()):
            m.playback_error = m.playback_requested and d['status'] == 'error'
            m.playback_requested = False
        m.lease_token = ''; m.lease_until = None; m.lease_action = ''
        m.save()
        if m.queued and m.cache_key and m.cache_until and m.cache_until > timezone.now():
            from .tasks import recognize_cached
            transaction.on_commit(lambda: recognize_cached(m.pk))
    return Response({'ok': True})


class BoundedUpload(FileUploadHandler):
    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > cache.MAX_BYTES:
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None


@api_view(['POST'])
@require_drain_token
def upload(request, pk):
    m = get_object_or_404(Media, pk=pk, active=True, post__active=True)
    if int(request.META.get('CONTENT_LENGTH') or 0) > cache.MAX_BYTES + 1024 * 1024:
        return Response({'error': 'Upload exceeds limit'}, status=413)
    request._request.upload_handlers.insert(0, BoundedUpload(request._request))
    f = request.FILES.get('file')
    if not f:
        raise ValidationError('Missing file')
    if (m.kind == 'video') != (f.content_type == 'video/mp4'):
        raise ValidationError('Media type does not match source')
    key = cache.store(f)
    try:
        with transaction.atomic():
            m = Media.objects.select_for_update().get(pk=m.pk)
            previous = m.cache_key
            m.cache_key = key; m.cache_until = timezone.now() + cache.TTL; m.save()
        if previous:
            cache.path(previous).unlink(missing_ok=True)
    except Exception:
        cache.path(key).unlink(missing_ok=True)
        raise
    cache.cleanup()
    return Response({'ok': True})


@api_view(['GET', 'HEAD'])
def stream(request, pk):
    # Requires the existing outer browser auth boundary and a private origin;
    # Django does not authenticate browser routes. Verify ingress before release.
    # Files are never served under MEDIA_URL, static, or a Mac web server.
    m = get_object_or_404(Media, pk=pk, active=True, post__active=True, cache_until__gt=timezone.now())
    if not m.cache_key:
        return Response(status=410)
    try:
        f = cache.path(m.cache_key).open('rb')
    except FileNotFoundError:
        return Response(status=410)
    header = f.read(12); f.seek(0)
    mime = 'video/mp4' if m.kind == 'video' else 'image/png' if header.startswith(b'\x89PNG') else 'image/jpeg'
    size = os.fstat(f.fileno()).st_size
    start, end, code = 0, size-1, 200
    value = request.headers.get('Range')
    if value:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
        if not match or not any(match.groups()):
            f.close(); return HttpResponse(status=416, headers={'Content-Range': f'bytes */{size}'})
        a, b = match.groups()
        start = int(a) if a else max(0, size-int(b))
        end = min(int(b), size-1) if a and b else size-1
        if start > end or start >= size:
            f.close(); return HttpResponse(status=416, headers={'Content-Range': f'bytes */{size}'})
        code = 206
    def chunks():
        try:
            f.seek(start); remaining = end-start+1
            while remaining:
                chunk = f.read(min(65536, remaining))
                if not chunk: break
                remaining -= len(chunk); yield chunk
        finally:
            f.close()
    if request.method == 'HEAD':
        f.close(); response = HttpResponse(status=code, content_type=mime)
    else:
        response = StreamingHttpResponse(chunks(), status=code, content_type=mime)
    response['Content-Length'] = end-start+1
    response['Accept-Ranges'] = 'bytes'; response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    if code == 206: response['Content-Range'] = f'bytes {start}-{end}/{size}'
    return response
