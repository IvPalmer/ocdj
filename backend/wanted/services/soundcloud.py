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


def _fetch_via_api(url, client_id, op=None):
    """Fetch playlist/set tracks using the SoundCloud API."""
    data = _resolve_url(url, client_id)

    if op and data.get('title') and not op.playlist_name:
        op.playlist_name = data['title']
        op.save()

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


def _parse_sc_url(url):
    """Extract artist/title from a SoundCloud URL slug as fallback."""
    # URL format: https://soundcloud.com/{user}/{track-slug}
    from urllib.parse import urlparse
    path = urlparse(url).path.strip('/')
    parts = path.split('/')
    if len(parts) >= 2:
        user = parts[0].replace('-', ' ')
        slug = parts[1].replace('-', ' ')
        return user, slug
    return '', url


def _extract_single_track(track_url):
    """Extract metadata for a single SoundCloud track."""
    import yt_dlp

    opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(track_url, download=False)
    except Exception:
        return None


def _fetch_via_ytdlp(url, op=None):
    """Fetch playlist using yt-dlp. Parallelizes track metadata fetching for SoundCloud."""
    import yt_dlp
    from concurrent.futures import ThreadPoolExecutor

    # Get the track list with extract_flat (fast — single request)
    flat_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'skip_download': True,
    }

    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if op and info.get('title') and not op.playlist_name:
        op.playlist_name = info['title']
        op.save()

    entries = list(info.get('entries', []))
    if not entries and info.get('title'):
        entries = [info]

    # Check if flat extraction gave us metadata
    has_metadata = any(
        (e.get('title') or e.get('track') or e.get('artist'))
        for e in entries[:3] if e
    )

    if has_metadata:
        return _parse_entries(entries)

    # SoundCloud flat extraction has no metadata — fetch each track in parallel
    track_urls = [e.get('url') for e in entries if e and e.get('url')]

    with ThreadPoolExecutor(max_workers=20) as pool:
        full_entries = list(pool.map(_extract_single_track, track_urls))

    return _parse_entries(full_entries)


def _parse_entries(entries):
    """Parse yt-dlp entries into track dicts."""
    tracks = []
    for entry in entries:
        if not entry:
            continue

        uploader = entry.get('uploader') or ''
        artist = entry.get('artist') or ''
        title = entry.get('track') or ''
        source_url = entry.get('webpage_url') or entry.get('url', '')

        if not title:
            raw = entry.get('title', '')
            if raw:
                parsed = parse_video_title(raw)
                artist = parsed['artist'] or uploader
                title = parsed['title']
                raw_title = parsed['raw_title']
            elif source_url:
                artist, title = _parse_sc_url(source_url)
                raw_title = f"{artist} - {title}" if artist else title
            else:
                continue
        else:
            if not artist:
                artist = uploader
            raw_title = entry.get('title', '')

        tracks.append({
            'artist': artist.strip(),
            'title': title.strip(),
            'raw_title': raw_title,
            'source_url': source_url,
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
                tracks = _fetch_via_api(op.url, config['client_id'], op)
            except Exception as api_err:
                logger.warning(f'SoundCloud API failed, falling back to yt-dlp: {api_err}')
                tracks = _fetch_via_ytdlp(op.url, op)
        else:
            tracks = _fetch_via_ytdlp(op.url, op)

        tracks = check_duplicates(tracks)

        duplicates = sum(1 for t in tracks if t.get('is_duplicate'))
        op.preview_data = tracks
        op.total_found = len(tracks)
        op.duplicates_found = duplicates
        op.status = 'previewing'
        op.save()

        from . import save_default_playlist_name
        save_default_playlist_name('soundcloud', op.playlist_name)

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
