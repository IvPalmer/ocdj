#!/usr/bin/env python3
"""Explicit, bounded Mac run. Cookies and upstream URLs never leave this process."""
import argparse
import asyncio
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


def parse_posts(items):
    posts, urls = [], {}
    for wrapper in items:
        p = wrapper.get('media', wrapper)
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
    from yt_dlp.cookies import extract_cookies_from_browser
    jar = extract_cookies_from_browser('chrome', profile=profile)
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


def instagram_json(session, path, params=None):
    r = session.get(IG + path, params=params, timeout=30, allow_redirects=False)
    if r.status_code in (301, 302, 401): raise ConnectorError('session-expired')
    if r.status_code == 429: raise ConnectorError('rate-limited')
    if r.status_code == 403: raise ConnectorError('restricted')
    if r.status_code != 200: raise ConnectorError('error')
    data = r.json()
    if data.get('message') == 'login_required': raise ConnectorError('session-expired')
    if data.get('status') != 'ok': raise ConnectorError('restricted')
    return data


def read_collection(session):
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
            return parse_posts(items)
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


def download(url, dest):
    # Deliberately use a separate, cookie-free session for CDN bytes.
    with requests.get(cdn_url(url), stream=True, timeout=(10, 45), allow_redirects=False) as r:
        if r.status_code != 200 or r.headers.get('Content-Type', '').split(';')[0] not in ['video/mp4', 'image/jpeg', 'image/png']:
            raise ConnectorError('error')
        total = 0
        deadline = time.monotonic() + 90
        with open(dest, 'wb') as out:
            for chunk in r.iter_content(65536):
                if time.monotonic() > deadline: raise ConnectorError('error')
                total += len(chunk)
                if total > MAX_BYTES: raise ConnectorError('error')
                out.write(chunk)
        return r.headers['Content-Type'].split(';')[0]


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

    def post(self, endpoint, data=None, files=None):
        r = self.session.post(self.base+endpoint, json=data if not files else None,
                              files=files, timeout=90, allow_redirects=False)
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
    ffmpeg = ffmpeg_path() if args.fixture else None
    try:
        if args.fixture:
            data = json.loads(Path(args.fixture).read_text()); urls = {}; session = None
        else:
            session = instagram_session(args.profile)
            data, urls = read_collection(session)
        mapping = api.post('sync/', data)['media']
        api.post('heartbeat/', {'status': 'ready'})
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
            while jobs < 10:
                api.post('heartbeat/', {'status': 'ready'})
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
                                    if str(exc) in STOP_STATUSES: raise
                                    comments, comment_status = [], 'unavailable'
                                matches, records = explicit_candidates('', job['position'], comments)
                                evidence = {'source': 'comment' if matches else 'shazam',
                                            'certainty': 'tentative', 'comments': comment_status, 'records': records}
                            if not matches:
                                source_mime = download(read_source(session, job), video)
                                with video.open('rb') as f:
                                    api.post(f"media/{job['id']}/upload/", files={'file': ('source', f, source_mime)})
                                if job.get('action', 'recognize') == 'recognize':
                                    ffmpeg = ffmpeg or ffmpeg_path()
                                    matches = asyncio.run(recognize(video, directory, ffmpeg))
                        status = 'identified' if matches else 'no-match'
                    except Exception as exc:
                        status, matches = 'error', []
                        if isinstance(exc, ConnectorError) and str(exc) in STOP_STATUSES:
                            stop_error = exc
                    finally:
                        video.unlink(missing_ok=True)
                    evidence['outcome'] = status
                    api.post(f"media/{job['id']}/complete/", {'token': job['token'], 'status': status, 'candidates': matches, 'recognition': evidence})
                    # Keep the per-media failure, then stop before any new claim
                    # or Instagram request. The outer handler publishes the state.
                    if stop_error is not None:
                        raise stop_error
                elif time.monotonic() >= deadline: break
                else: time.sleep(3)
    except ConnectorError as e:
        status = str(e) if str(e) in STOP_STATUSES else 'error'
        api.post('heartbeat/', {'status': status})
        raise ConnectorError(status) from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', required=True, help='OCDJ origin; bearer comes from existing DRAIN_TOKEN env')
    parser.add_argument('--profile', default='Profile 1')
    parser.add_argument('--fixture', help='Offline synthetic fixture, never live Instagram data')
    parser.add_argument('--work-seconds', type=int, default=0, choices=range(0, 3601))
    try:
        run(parser.parse_args())
    except Exception as exc:
        # Do not log HTTP bodies, cookie values, CDN URLs, or exception details.
        print('Connector stopped:', str(exc) if isinstance(exc, ConnectorError) else 'error')
        raise SystemExit(1)
