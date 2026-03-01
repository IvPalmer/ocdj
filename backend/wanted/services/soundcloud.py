import json
import logging
import os
import re
import threading
import urllib.request
import urllib.parse

from django import db
from django.conf import settings

from wanted.models import ImportOperation
from .parsers import parse_video_title
from .dedup import check_duplicates

logger = logging.getLogger(__name__)


def _get_config():
    from core.views import get_config
    return {
        'client_id': get_config('SC_CLIENT_ID'),
        'client_secret': get_config('SC_CLIENT_SECRET'),
    }


def _resolve_url(url, client_id):
    """Resolve a SoundCloud URL to its API representation."""
    api_url = f'https://api.soundcloud.com/resolve?url={urllib.parse.quote(url, safe="")}&client_id={client_id}'
    req = urllib.request.Request(api_url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _fetch_via_api(url, client_id):
    """Fetch playlist/set tracks using the SoundCloud API."""
    data = _resolve_url(url, client_id)

    # Could be a playlist/set or a user's likes/tracks
    tracks_data = data.get('tracks', [])
    if not tracks_data:
        # Maybe it resolved to a single track
        if data.get('kind') == 'track':
            tracks_data = [data]

    tracks = []
    for track in tracks_data:
        artist = track.get('user', {}).get('username', '')
        title = track.get('title', '')

        # SoundCloud titles often contain "Artist - Title"
        if ' - ' in title and not artist:
            parsed = parse_video_title(title)
            artist = parsed['artist'] or artist
            title = parsed['title']
            raw_title = parsed['raw_title']
        else:
            raw_title = f"{artist} - {title}" if artist else title

        tracks.append({
            'artist': artist.strip(),
            'title': title.strip(),
            'raw_title': raw_title,
            'source_url': track.get('permalink_url', ''),
        })

    return tracks


def _fetch_via_ytdlp(url):
    """Fetch playlist using yt-dlp (fallback)."""
    import yt_dlp

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'skip_download': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get('entries', [])
    if not entries and info.get('title'):
        entries = [info]

    tracks = []
    for entry in entries:
        if not entry:
            continue

        artist = entry.get('artist') or entry.get('uploader') or ''
        title = entry.get('track') or ''

        if not title:
            parsed = parse_video_title(entry.get('title', ''))
            artist = parsed['artist'] or artist
            title = parsed['title']
            raw_title = parsed['raw_title']
        else:
            raw_title = entry.get('title', '')

        tracks.append({
            'artist': artist.strip(),
            'title': title.strip(),
            'raw_title': raw_title,
            'source_url': entry.get('url') or entry.get('webpage_url', ''),
        })

    return tracks


def run_soundcloud_import(operation_id):
    """Fetch a SoundCloud playlist/set and parse tracks. Runs in a background thread."""
    thread = threading.Thread(
        target=_soundcloud_worker,
        args=(operation_id,),
        daemon=True,
    )
    thread.start()


def _soundcloud_worker(operation_id):
    try:
        op = ImportOperation.objects.get(pk=operation_id)
        op.status = 'fetching'
        op.save()

        config = _get_config()

        # Try SoundCloud API first (supports private playlists), fallback to yt-dlp
        if config['client_id']:
            try:
                tracks = _fetch_via_api(op.url, config['client_id'])
            except Exception as api_err:
                logger.warning(f'SoundCloud API failed, falling back to yt-dlp: {api_err}')
                tracks = _fetch_via_ytdlp(op.url)
        else:
            tracks = _fetch_via_ytdlp(op.url)

        tracks = check_duplicates(tracks)

        duplicates = sum(1 for t in tracks if t.get('is_duplicate'))
        op.preview_data = tracks
        op.total_found = len(tracks)
        op.duplicates_found = duplicates
        op.status = 'previewing'
        op.save()

    except Exception as e:
        logger.exception(f'SoundCloud import failed for operation {operation_id}')
        try:
            op = ImportOperation.objects.get(pk=operation_id)
            op.status = 'failed'
            op.error_message = str(e)
            op.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()
