#!/usr/bin/env python3
"""Explicit Mac playback connector. No listener, scan or install.

Run with a caller-owned loopback SSH forward to the private backend, an inherited
DRAIN_TOKEN, and the exact snapshot root. Paths and source tags never leave Mac.
"""
import argparse
import fcntl
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import requests

from tools.mac_library.reveal_map import default_map_path, review_archive_path

MAX_SOURCE = 512 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
MAX_MAP = 32 * 1024 * 1024
DEMUXERS = {'.mp3': 'mp3', '.flac': 'flac', '.wav': 'wav', '.aif': 'aiff',
            '.aiff': 'aiff', '.ogg': 'ogg', '.m4a': 'mov'}
COLLECTION = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
OPAQUE = re.compile(r'[0-9a-f]{64}')


class Refused(ValueError):
    pass


def components(value):
    if not isinstance(value, str) or '\0' in value:
        raise Refused()
    parts = value.split('/')
    if not parts or any(p in ('', '.', '..') for p in parts):
        raise Refused()
    return parts


def open_absolute(value, directory=False):
    """Walk without following any symlink, including ancestors."""
    if not isinstance(value, str) or not value.startswith('/'):
        raise Refused()
    parts = components(value[1:])
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(parts) - 1 or directory:
                flags |= os.O_DIRECTORY
            nxt = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def load_map(filename, root):
    fd = open_absolute(str(filename))
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or not 0 < info.st_size <= MAX_MAP):
            raise Refused()
        data = json.loads(handle.read(MAX_MAP + 1))
    if (data.get('version') != 1 or not isinstance(data.get('collection_id'), str)
            or not COLLECTION.fullmatch(data['collection_id']) or data.get('root') != root
            or not isinstance(data.get('tracks'), dict) or not 1 <= len(data['tracks']) <= 100000):
        raise Refused()
    return data


@contextmanager
def source(map_data, collection, opaque):
    if (not isinstance(opaque, str) or not OPAQUE.fullmatch(opaque)
            or collection != map_data['collection_id'] or opaque not in map_data['tracks']):
        raise Refused()
    relative = map_data['tracks'][opaque]
    parts = components(relative)
    if any(p.casefold() in ('id3', 'traxdb') for p in parts) and not review_archive_path(parts):
        raise Refused()
    demuxer = DEMUXERS.get(Path(relative).suffix.lower())
    if not demuxer:
        raise Refused()
    fd = open_absolute(map_data['root'], directory=True)
    try:
        for i, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(parts) - 1:
                flags |= os.O_DIRECTORY
            nxt = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = nxt
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_SOURCE:
            raise Refused()
        yield fd, demuxer
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Refused()
    finally:
        os.close(fd)


def transcode(fd, demuxer, destination, ffmpeg='ffmpeg'):
    subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-protocol_whitelist', 'file,pipe', '-f', demuxer, '-i', f'/dev/fd/{fd}',
                    '-map', '0:a:0', '-vn', '-map_metadata', '-1', '-map_chapters', '-1',
                    '-ac', '2', '-ar', '48000', '-c:a', 'libmp3lame', '-b:a', '192k',
                    '-fs', str(MAX_OUTPUT + 1), '-f', 'mp3', str(destination)],
                   pass_fds=(fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=120, check=True)
    if not 12 <= destination.stat().st_size <= MAX_OUTPUT:
        raise Refused()


class API:
    def __init__(self, base, token):
        url = urlsplit(base)
        # Deliberately requires a caller-owned private SSH loopback forward.
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
                or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment or not token):
            raise Refused()
        self.base = base.rstrip('/') + '/api/library/playback/'
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers['Authorization'] = 'Bearer ' + token

    def post(self, endpoint, *, data=None, audio=None, lease=None):
        headers = {'X-Playback-Lease': lease} if lease else {}
        if audio is not None:
            headers['Content-Type'] = 'audio/mpeg'
        response = self.session.post(self.base + endpoint, headers=headers,
                                     json=data if audio is None else None, data=audio,
                                     timeout=(5, 60), allow_redirects=False)
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            # No automatic resend, including ambiguous upload/claim results.
            raise Refused()
        return response.json()


def serve_one(api, map_data, *, ffmpeg='ffmpeg'):
    job = api.post('claim/', data={'collection_id': map_data['collection_id']})
    if job is None:
        return False
    # Validate every field before constructing even a loopback request endpoint.
    if (type(job.get('track_id')) is not int or job['track_id'] < 1
            or not isinstance(job.get('token'), str) or not re.fullmatch('[0-9a-f]{48}', job['token'])):
        raise Refused()
    endpoint = f"{job['track_id']}/complete/"
    with tempfile.TemporaryDirectory(prefix='ocdj-playback-') as temporary:
        try:
            output = Path(temporary) / 'audio.mp3'
            with source(map_data, job.get('collection_id'), job.get('opaque_id')) as (fd, demuxer):
                transcode(fd, demuxer, output, ffmpeg)
        except (OSError, ValueError, subprocess.SubprocessError):
            api.post(endpoint, data={}, lease=job['token'])
            return True
        with output.open('rb') as audio:
            # Transport failures propagate: never send a second result after ambiguity.
            api.post(endpoint, audio=audio, lease=job['token'])
    return True


@contextmanager
def singleton(filename=None):
    # Stable per-user path across launch contexts; never unlink a flock inode.
    filename = filename or Path.home() / '.ocdj-mac-playback.lock'
    fd = os.open(filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise Refused()
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def listen(api, data, *, seconds=900, ffmpeg='ffmpeg'):
    deadline = time.monotonic() + seconds if seconds is not None else None
    while deadline is None or time.monotonic() < deadline:
        if not serve_one(api, data, ffmpeg=ffmpeg):
            time.sleep(2 if deadline is None else min(2, max(0, deadline - time.monotonic())))


def interrupted(signum, frame):
    # subprocess.run kills/waits for in-flight FFmpeg on this exception; temporary
    # files and the singleton lock unwind normally. No completion is resent.
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--map', type=Path, default=default_map_path())
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--seconds', type=int, default=900)
    mode.add_argument('--login-session', action='store_true',
                      help='Listen until Ctrl-C, logout/HUP or TERM; no restart or installation')
    parser.add_argument('--ffmpeg', default='ffmpeg', help='Existing executable; nothing is downloaded or installed')
    args = parser.parse_args()
    previous = {sig: signal.signal(sig, interrupted) for sig in
                (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        if not 1 <= args.seconds <= 1800:
            raise Refused()
        with singleton():
            data = load_map(args.map, args.root)
            fd = open_absolute(args.root, directory=True)
            os.close(fd)
            api = API(args.api, os.environ.get('DRAIN_TOKEN', ''))
            print('Playback connector listening; Ctrl-C stops; no automatic restart.', flush=True)
            listen(api, data, seconds=None if args.login_session else args.seconds, ffmpeg=args.ffmpeg)
    except KeyboardInterrupt:
        print('Playback connector stopped by session/operator; no restart.', flush=True)
        return 130
    except Exception:
        print('Playback connector stopped: singleton, private transport, map, source or staging unavailable. No automatic retry.', file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


if __name__ == '__main__':
    sys.exit(main())
