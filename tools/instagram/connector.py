#!/usr/bin/env python3
"""Explicit Mac run, bounded by default with opt-in login-session residency. Cookies and upstream URLs never leave this process."""
import argparse
import asyncio
import base64
from io import BytesIO
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import requests

COLLECTION = '18117783274346248'
MAX_BYTES = 64 * 1024 * 1024
IG = 'https://www.instagram.com'
STOP_STATUSES = frozenset(['session-expired', 'restricted', 'rate-limited'])
IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_PIXELS = 4_000_000
JPEG_BYTES = 128 * 1024
IMAGE_SECONDS = 10
THUMBNAIL_SECONDS = 300


class ConnectorError(Exception):
    pass


def cdn_url(url):
    u = urlsplit(url)
    if (u.scheme != 'https' or u.username or u.password or u.port not in (None, 443)
            or not any((u.hostname or '').endswith('.'+d) for d in ['cdninstagram.com', 'fbcdn.net'])):
        raise ConnectorError('restricted')
    # Defense against DNS rebinding/private addresses; no redirect following.
    for address in socket.getaddrinfo(u.hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ConnectorError('restricted')
    return url


def poster_url(post):
    # Video image_versions2 are posters too. Never use video_versions here.
    for source in [post] + (post.get('carousel_media') or []):
        candidates = [v for v in (source.get('image_versions2') or {}).get('candidates') or []
                      if isinstance(v, dict) and isinstance(v.get('width'), int) and isinstance(v.get('height'), int)
                      and v['width'] > 0 and v['height'] > 0
                      and v['width'] * v['height'] <= IMAGE_PIXELS
                      and isinstance(v.get('url'), str)]
        if candidates:
            return min(candidates, key=lambda v: v['width'] * v['height'])['url']


def parse_posts(items, thumbnail_urls=None):
    posts, urls = [], {}
    for wrapper in items:
        p = wrapper.get('media', wrapper)
        if thumbnail_urls is not None:
            poster = poster_url(p)
            if poster:
                thumbnail_urls[str(p['pk'])] = poster
        children = p.get('carousel_media') or [p]
        if len(children) > 20:
            raise ConnectorError('restricted')
        parsed = []
        for ch in children:
            ident = str(ch['pk'])
            kind = 'video' if ch.get('media_type') == 2 else 'image'
            parsed.append({'id': ident, 'kind': kind})
            if kind == 'image':
                images = (ch.get('image_versions2') or {}).get('candidates') or []
                if images: urls[ident] = min(images, key=lambda v: v.get('width', 10000))['url']
            if kind == 'video':
                versions = ch.get('video_versions') or []
                # Prefer the smallest provided rendition for the bounded pilot.
                if versions:
                    urls[ident] = min(versions, key=lambda v: v.get('width', 10000))['url']
        posts.append({'id': str(p['pk']), 'permalink': f"{IG}/p/{p['code']}/",
                      'author': p.get('user', {}).get('username', ''),
                      'caption': (p.get('caption') or {}).get('text', '')[:10000], 'children': parsed})
    return {'collection_id': COLLECTION, 'posts': posts}, urls


def instagram_session(profile):
    from yt_dlp import cookies
    # The upstream macOS helper spawns `security`, which can prompt. Replace
    # only that helper during extraction, using the same key via no-UI API.
    if sys.platform == 'darwin':
        from unittest.mock import patch
        try:
            from .mac_access import keyring_password
        except ImportError:  # direct script entry point
            from mac_access import keyring_password
        with patch.object(cookies, '_get_mac_keyring_password', keyring_password):
            jar = cookies.extract_cookies_from_browser('chrome', profile=profile)
    else:
        jar = cookies.extract_cookies_from_browser('chrome', profile=profile)
    session = requests.Session()
    for cookie in jar:
        if cookie.domain.lstrip('.') == 'instagram.com' or cookie.domain.endswith('.instagram.com'):
            session.cookies.set_cookie(cookie)
    session.headers.update({'X-IG-App-ID': '936619743392459',
                            'User-Agent': 'Mozilla/5.0', 'Referer': IG+'/'})
    if not session.cookies.get_dict().get('sessionid'):
        raise ConnectorError('session-expired')
    del jar
    return session


RETRY_DELAYS = (5, 15, 30)


def safe_request(request):
    """Bounded recovery for reads/status only. Never wrap claim/upload/complete."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = request()
            if response.status_code not in (502, 503, 504):
                return response
            response.close()
        except (requests.ConnectionError, requests.Timeout):
            pass
        if attempt == len(RETRY_DELAYS):
            raise ConnectorError('network-unavailable') from None
        time.sleep(RETRY_DELAYS[attempt])


def instagram_json(session, path, params=None):
    r = safe_request(lambda: session.get(IG + path, params=params, timeout=30, allow_redirects=False))
    if r.status_code in (301, 302, 303, 307, 308, 401): raise ConnectorError('session-expired')
    if r.status_code == 429: raise ConnectorError('rate-limited')
    if r.status_code == 403: raise ConnectorError('restricted')
    if r.status_code != 200: raise ConnectorError('error')
    data = r.json()
    if data.get('message') == 'login_required': raise ConnectorError('session-expired')
    if data.get('status') != 'ok': raise ConnectorError('restricted')
    return data


def read_collection(session, thumbnail_urls=None):
    """Commit only a complete snapshot, deduplicated in saved response order."""
    items, seen, cursors, cursor = [], set(), set(), None
    for _ in range(120):
        params = {'count': 20}
        if cursor: params['max_id'] = cursor
        data = instagram_json(session, f'/api/v1/feed/collection/{COLLECTION}/posts/', params)
        for item in data.get('items', []):
            ident = str(item.get('media', item)['pk'])
            if ident not in seen:
                seen.add(ident); items.append(item)
        if len(items) > 2000: raise ConnectorError('collection exceeds bound')
        if not data.get('more_available'):
            return parse_posts(items, thumbnail_urls=thumbnail_urls)
        cursor = data.get('next_max_id')
        if not cursor or cursor in cursors: break
        cursors.add(cursor)
    raise ConnectorError('incomplete collection snapshot')


def read_source(session, job):
    """Refresh one selected post, never rescan the collection for a media job."""
    data = instagram_json(session, f"/api/v1/media/{job['post_instagram_id']}/info/")
    _, urls = parse_posts(data.get('items', []))
    if job['instagram_id'] not in urls: raise ConnectorError('source-unavailable')
    return urls[job['instagram_id']]


def read_comments(session, post_id):
    # One page only. Never claim these are the complete comment history.
    data = instagram_json(session, f'/api/v1/media/{post_id}/comments/', {'can_support_threading': 'true'})
    return [{'id': str(c['pk']), 'text': c.get('text', '')[:2000]}
            for c in data.get('comments', [])[:20] if c.get('pk')]


def download(url, dest, *, image_only=False):
    # Deliberately use a separate, cookie-free session for CDN bytes.
    deadline = time.monotonic() + (IMAGE_SECONDS if image_only else 90)
    limit = IMAGE_BYTES if image_only else MAX_BYTES
    with requests.get(cdn_url(url), stream=True, timeout=(3, 3) if image_only else (10, 45),
                      headers={'Accept-Encoding': 'identity'} if image_only else None,
                      allow_redirects=False) as r:
        if r.status_code in (301, 302, 303, 307, 308, 401, 403, 429):
            raise ConnectorError({401: 'session-expired', 429: 'rate-limited'}.get(r.status_code, 'restricted'))
        mime = r.headers.get('Content-Type', '').split(';')[0]
        allowed = ['image/jpeg', 'image/png'] if image_only else ['video/mp4', 'image/jpeg', 'image/png']
        if r.status_code != 200 or mime not in allowed:
            raise ConnectorError('error')
        if image_only and (r.headers.get('Content-Encoding', 'identity') != 'identity'
                           or int(r.headers.get('Content-Length', 0)) > limit):
            raise ConnectorError('error')
        total = 0
        # read1 returns available bytes after one read, so a trickling peer
        # cannot keep an iter_content chunk filling beyond the total deadline.
        if image_only:
            read1 = getattr(r.raw, 'read1', None)
            # Older urllib3 lacks read1: single-byte chunks still check the
            # deadline on trickling responses without adding a dependency.
            chunks = iter(lambda: read1(65536), b'') if read1 else r.iter_content(1)
        else:
            chunks = r.iter_content(65536)
        with open(dest, 'wb') as out:
            for chunk in chunks:
                if time.monotonic() > deadline: raise ConnectorError('error')
                total += len(chunk)
                if total > limit: raise ConnectorError('error')
                out.write(chunk)
        if time.monotonic() > deadline: raise ConnectorError('error')
        return r.headers['Content-Type'].split(';')[0]


def thumbnail_jpeg(raw):
    from PIL import Image
    if len(raw) > IMAGE_BYTES:
        raise ConnectorError('error')
    try:
        with Image.open(BytesIO(raw), formats=['JPEG', 'PNG']) as image:
            if image.width * image.height > IMAGE_PIXELS or getattr(image, 'n_frames', 1) != 1:
                raise ConnectorError('error')
            image.thumbnail((320, 320))
            # New pixels discard EXIF, comments, ICC profiles and other metadata.
            clean = Image.new('RGB', image.size)
            clean.paste(image.convert('RGB'))
            out = BytesIO()
            clean.save(out, format='JPEG', quality=75)
            jpeg = out.getvalue()
            if len(jpeg) > JPEG_BYTES: raise ConnectorError('error')
            return jpeg
    except Exception:
        raise ConnectorError('error') from None


def capture_thumbnail(url):
    deadline = time.monotonic() + IMAGE_SECONDS
    try:
        with tempfile.TemporaryDirectory(prefix='ocdj-instagram-poster-') as directory:
            source = Path(directory) / 'image'
            download(url, source, image_only=True)
            if time.monotonic() > deadline: raise ConnectorError('error')
            jpeg = thumbnail_jpeg(source.read_bytes())
        if time.monotonic() > deadline: raise ConnectorError('error')
        return jpeg
    except ConnectorError:
        raise
    except Exception:
        # Includes low-level streaming errors, without disclosing CDN URLs.
        raise ConnectorError('error') from None


class ThumbnailCapture:
    """One image per idle job poll, one attempt per post in this explicit run."""
    def __init__(self, sync, urls):
        self.token = sync.get('sync_token')
        self.pending = []
        self.queue = []
        if isinstance(self.token, str) and 0 < len(self.token) <= 512:
            seen = set()
            for post in sync.get('thumbnail_posts', []):
                ident = post.get('post_id')
                if (isinstance(ident, str) and ident.isascii() and ident.isdigit() and len(ident) <= 40
                        and post.get('available') is False and ident in urls and ident not in seen):
                    self.queue.append((ident, urls[ident]))
                    seen.add(ident)
        self.queue = iter(self.queue)
        self.deadline = time.monotonic() + THUMBNAIL_SECONDS
        self.stopped = False

    def flush(self, api):
        if self.pending:
            result = api.post('thumbnails/', {'sync_token': self.token, 'thumbnails': self.pending}, timeout=10)
            self.pending = []
            # 409 fences this entire run. Retry/inactive results are left for
            # a later explicit sync, as are ambiguous upload failures.
            if result is None:
                self.stopped = True

    def step(self, api):
        if self.stopped: return False
        item = next(self.queue, None) if time.monotonic() < self.deadline else None
        if item is None:
            self.flush(api)
            self.stopped = True
            return False
        ident, url = item
        try:
            jpeg = capture_thumbnail(url)
        except ConnectorError as exc:
            if str(exc) in STOP_STATUSES: raise
        else:
            self.pending.append({'post_id': ident, 'jpeg': base64.b64encode(jpeg).decode('ascii')})
        if len(self.pending) == 16:
            self.flush(api)
        return not self.stopped


class API:
    def __init__(self, base, token):
        u = urlsplit(base)
        if u.scheme != 'https' and not (u.scheme == 'http' and u.hostname in ['localhost', '127.0.0.1']):
            raise ConnectorError('API requires HTTPS')
        if u.username or u.password or u.query or u.fragment:
            raise ConnectorError('Invalid API base')
        self.base = base.rstrip('/')+'/api/instagram/connector/'
        self.session = requests.Session()
        self.session.headers['Authorization'] = 'Bearer '+token

    def post(self, endpoint, data=None, files=None, lease=None, timeout=90):
        headers = {'X-Instagram-Lease': lease} if lease else None
        def send():
            return self.session.post(self.base+endpoint, json=data if not files else None,
                                     files=files, headers=headers, timeout=timeout, allow_redirects=False)
        # Heartbeat is an idempotent status write. All other POSTs are attempted
        # exactly once, including a claim whose response may have been lost.
        try:
            r = safe_request(send) if endpoint == 'heartbeat/' else send()
        except (requests.ConnectionError, requests.Timeout):
            raise ConnectorError('ambiguous-transport') from None
        if r.status_code == 204: return None
        if r.status_code == 409: return None  # expired result; server owns fencing
        if r.status_code != 200: raise ConnectorError('API request failed')
        return r.json()


def ffmpeg_path():
    import imageio_ffmpeg
    binary = os.environ.get('INSTAGRAM_FFMPEG') or imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([binary, '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=10, check=True)
    return binary


# Keep the exact ffmpeg/Shazam sampling path shared with the existing backend.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.instagram.recognition import recognize, explicit_candidates


def run(args):
    token = os.environ.get('DRAIN_TOKEN', '')
    if not token: raise ConnectorError('DRAIN_TOKEN missing')
    api = API(args.api, token)
    resident = getattr(args, 'resident', False)
    if resident and args.fixture:
        raise ConnectorError('resident-fixture-disallowed')
    ffmpeg = ffmpeg_path() if args.fixture else None
    try:
        thumbnail_urls = {}
        if args.fixture:
            data = json.loads(Path(args.fixture).read_text()); urls = {}; session = None
        else:
            session = instagram_session(args.profile)
            data, urls = read_collection(session, thumbnail_urls=thumbnail_urls)
        synced = api.post('sync/', data)
        mapping = synced['media']
        thumbnails = ThumbnailCapture(synced, thumbnail_urls)
        api.post('heartbeat/', {'status': 'ready'})
        refresh_at = time.monotonic() + 3600
        last_poll = time.time()
        # One original at a time; TemporaryDirectory owns every local byte.
        with tempfile.TemporaryDirectory(prefix='ocdj-instagram-') as directory:
            video = Path(directory) / 'child.mp4'
            # Metadata first: live sync downloads no videos. Synthetic fixture
            # keeps eager uploads solely for deterministic isolated browser QA.
            for media in mapping if args.fixture else []:
                if media['kind'] != 'video': continue
                subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x29241f:s=640x360:d=3',
                                '-f', 'lavfi', '-i', 'sine=frequency=220:duration=3', '-c:v', 'libx264',
                                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', '-movflags', '+faststart', str(video)],
                               check=True, timeout=30)
                with video.open('rb') as f:
                    api.post(f"media/{media['id']}/upload/", files={'file': ('child.mp4', f, 'video/mp4')})
                video.unlink()
            deadline = time.monotonic()+args.work_seconds
            jobs = 0
            while resident or jobs < 10:
                # A wake/long scheduling gap does not replay old work or sync
                # repeatedly. Refresh once, from a complete snapshot only.
                now = time.time()
                woke = now - last_poll > 180
                last_poll = now
                api.post('heartbeat/', {'status': 'ready'})
                if resident and (woke or time.monotonic() >= refresh_at):
                    thumbnail_urls = {}
                    data, _ = read_collection(session, thumbnail_urls=thumbnail_urls)
                    synced = api.post('sync/', data)
                    thumbnails = ThumbnailCapture(synced, thumbnail_urls)
                    refresh_at = time.monotonic() + 3600
                job = api.post('claim/')
                if job:
                    jobs += 1
                    stop_error = None
                    evidence = {'source': 'shazam', 'certainty': 'tentative', 'comments': 'not-read'}
                    try:
                        if args.fixture:
                            matches = [{'artist': 'Fixture artist', 'title': 'Synthetic preview tone'}]
                        else:
                            matches = []
                            if job.get('action', 'recognize') == 'recognize':
                                try:
                                    comments = read_comments(session, job['post_instagram_id'])
                                    comment_status = 'partial'
                                except ConnectorError as exc:
                                    if str(exc) in STOP_STATUSES | {'network-unavailable'}: raise
                                    comments, comment_status = [], 'unavailable'
                                matches, records = explicit_candidates('', job['position'], comments)
                                evidence = {'source': 'comment' if matches else 'shazam',
                                            'certainty': 'tentative', 'comments': comment_status, 'records': records}
                            if not matches:
                                source_mime = download(read_source(session, job), video)
                                with video.open('rb') as f:
                                    uploaded = api.post(f"media/{job['id']}/upload/",
                                                        files={'file': ('source', f, source_mime)}, lease=job['token'])
                                if uploaded is None:  # Stale job, including a correction during download.
                                    continue
                                if job.get('action', 'recognize') == 'recognize':
                                    ffmpeg = ffmpeg or ffmpeg_path()
                                    matches = asyncio.run(recognize(video, directory, ffmpeg))
                        status = 'identified' if matches else 'no-match'
                    except Exception as exc:
                        status, matches = 'error', []
                        if isinstance(exc, ConnectorError) and str(exc) in STOP_STATUSES | {'network-unavailable', 'ambiguous-transport'}:
                            stop_error = exc
                    finally:
                        video.unlink(missing_ok=True)
                    evidence['outcome'] = status
                    api.post(f"media/{job['id']}/complete/", {'token': job['token'], 'status': status, 'candidates': matches, 'recognition': evidence})
                    # Keep the per-media failure, then stop before any new claim
                    # or Instagram request. The outer handler publishes the state.
                    if stop_error is not None:
                        raise stop_error
                    if resident: time.sleep(15)  # Bound sustained claim rate too.
                elif thumbnails.step(api): continue
                elif not resident and time.monotonic() >= deadline: break
                else: time.sleep(15 if resident else 3)
            thumbnails.flush(api)
    except ConnectorError as e:
        status = str(e) if str(e) in STOP_STATUSES else 'error'
        try:
            if str(e) not in {'network-unavailable', 'ambiguous-transport'}:
                api.post('heartbeat/', {'status': status})
        except Exception:
            pass  # Preserve original restriction, including when transport died.
        raise ConnectorError(status) from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', required=True, help='OCDJ origin; bearer comes from existing DRAIN_TOKEN env')
    parser.add_argument('--profile', default='Profile 1')
    parser.add_argument('--fixture', help='Offline synthetic fixture, never live Instagram data')
    parser.add_argument('--resident', action='store_true', help='stay in this login session until stopped; no relaunch')
    parser.add_argument('--work-seconds', type=int, default=0, choices=range(0, 3601))
    try:
        run(parser.parse_args())
    except Exception as exc:
        # Do not log HTTP bodies, cookie values, CDN URLs, or exception details.
        print('Connector stopped:', str(exc) if isinstance(exc, ConnectorError) else 'error')
        raise SystemExit(1)
