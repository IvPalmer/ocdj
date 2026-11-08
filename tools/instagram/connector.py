#!/usr/bin/env python3
"""Explicit, bounded Mac run. Cookies and upstream URLs never leave this process."""
import argparse
import asyncio
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import requests

COLLECTION = '18117783274346248'
MAX_BYTES = 64 * 1024 * 1024
IG = 'https://www.instagram.com'


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
    for wrapper in items[:5]:
        p = wrapper.get('media', wrapper)
        children = p.get('carousel_media') or [p]
        if len(children) > 20:
            raise ConnectorError('restricted')
        parsed = []
        for ch in children:
            ident = str(ch['pk'])
            kind = 'video' if ch.get('media_type') == 2 else 'image'
            parsed.append({'id': ident, 'kind': kind})
            if kind == 'video':
                versions = ch.get('video_versions') or []
                if not versions:
                    raise ConnectorError('error')
                # Prefer the smallest provided rendition for the bounded pilot.
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


def read_collection(session):
    items, cursor = [], None
    for _ in range(5):
        params = {'count': 5}
        if cursor: params['max_id'] = cursor
        r = session.get(f'{IG}/api/v1/feed/collection/{COLLECTION}/posts/',
                        params=params, timeout=30, allow_redirects=False)
        if r.status_code in (301, 302, 401): raise ConnectorError('session-expired')
        if r.status_code == 429: raise ConnectorError('rate-limited')
        if r.status_code == 403: raise ConnectorError('restricted')
        if r.status_code != 200: raise ConnectorError('error')
        d = r.json()
        if d.get('message') == 'login_required': raise ConnectorError('session-expired')
        if d.get('status') != 'ok': raise ConnectorError('restricted')
        items.extend(d.get('items', []))
        cursor = d.get('next_max_id')
        if len(items) >= 5 or not d.get('more_available') or not cursor: break
    return parse_posts(items)


def download(url, dest):
    # Deliberately use a separate, cookie-free session for CDN bytes.
    with requests.get(cdn_url(url), stream=True, timeout=(10, 45), allow_redirects=False) as r:
        if r.status_code != 200 or r.headers.get('Content-Type', '').split(';')[0] != 'video/mp4':
            raise ConnectorError('error')
        total = 0
        deadline = time.monotonic() + 90
        with open(dest, 'wb') as out:
            for chunk in r.iter_content(65536):
                if time.monotonic() > deadline: raise ConnectorError('error')
                total += len(chunk)
                if total > MAX_BYTES: raise ConnectorError('error')
                out.write(chunk)


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


async def recognize(video, directory, ffmpeg):
    from shazamio import Shazam
    candidates = []
    for offset in (0, 30, 60):
        sample = Path(directory) / 'sample.wav'
        result = subprocess.run([ffmpeg, '-v', 'error', '-y', '-ss', str(offset), '-protocol_whitelist', 'file,pipe', '-i', str(video),
                                '-t', '20', '-vn', '-ac', '1', '-ar', '44100', str(sample)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        if result.returncode != 0:
            raise ConnectorError('error')
        if not sample.exists() or sample.stat().st_size < 1000:
            if offset == 0: raise ConnectorError('error')
            break
        response = await asyncio.wait_for(Shazam().recognize(str(sample)), timeout=40)
        track = response.get('track')
        if track:
            candidate = {'artist': track.get('subtitle', '')[:500], 'title': track.get('title', '')[:500]}
            if all(candidate.values()) and candidate not in candidates: candidates.append(candidate)
    return candidates


def run(args):
    token = os.environ.get('DRAIN_TOKEN', '')
    if not token: raise ConnectorError('DRAIN_TOKEN missing')
    api = API(args.api, token)
    ffmpeg = ffmpeg_path()
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
            for media in mapping:
                if media['kind'] != 'video': continue
                if args.fixture:
                    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x29241f:s=640x360:d=3',
                                    '-f', 'lavfi', '-i', 'sine=frequency=220:duration=3', '-c:v', 'libx264',
                                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', '-movflags', '+faststart', str(video)],
                                   check=True, timeout=30)
                else: download(urls[media['instagram_id']], video)
                with video.open('rb') as f:
                    api.post(f"media/{media['id']}/upload/", files={'file': ('child.mp4', f, 'video/mp4')})
                video.unlink()
            deadline = time.monotonic()+args.work_seconds
            while True:
                api.post('heartbeat/', {'status': 'ready'})
                job = api.post('claim/')
                if job:
                    try:
                        if args.fixture:
                            matches = [{'artist': 'Fixture artist', 'title': 'Synthetic preview tone'}]
                        else:
                            # Refresh collection URLs in memory before each job.
                            _, urls = read_collection(session)
                            download(urls[job['instagram_id']], video)
                            matches = asyncio.run(recognize(video, directory, ffmpeg))
                        status = 'identified' if matches else 'no-match'
                    except Exception:
                        status, matches = 'error', []
                    finally:
                        video.unlink(missing_ok=True)
                    api.post(f"media/{job['id']}/complete/", {'token': job['token'], 'status': status, 'candidates': matches})
                elif time.monotonic() >= deadline: break
                else: time.sleep(3)
    except ConnectorError as e:
        status = str(e) if str(e) in ['session-expired', 'restricted', 'rate-limited'] else 'error'
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
