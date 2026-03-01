import logging
import os
import re
import threading

from django import db
from django.conf import settings

from wanted.models import ImportOperation
from .parsers import parse_video_title
from .dedup import check_duplicates

logger = logging.getLogger(__name__)


def _get_api_key():
    return getattr(settings, 'YOUTUBE_API_KEY', '') or os.environ.get('YOUTUBE_API_KEY', '')


def _extract_playlist_id(url):
    """Extract playlist ID from a YouTube URL."""
    match = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None


def _fetch_via_api(playlist_id, api_key):
    """Fetch playlist items using the YouTube Data API v3. Works with private playlists if the key has access."""
    import urllib.request
    import json

    tracks = []
    page_token = ''

    while True:
        url = (
            f'https://www.googleapis.com/youtube/v3/playlistItems'
            f'?part=snippet&maxResults=50&playlistId={playlist_id}&key={api_key}'
        )
        if page_token:
            url += f'&pageToken={page_token}'

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())

        for item in data.get('items', []):
            snippet = item.get('snippet', {})
            video_title = snippet.get('title', '')
            channel = snippet.get('videoOwnerChannelTitle', '')
            video_id = snippet.get('resourceId', {}).get('videoId', '')

            parsed = parse_video_title(video_title)
            artist = parsed['artist'] or channel.replace(' - Topic', '')
            title = parsed['title']

            tracks.append({
                'artist': artist.strip(),
                'title': title.strip(),
                'raw_title': video_title,
                'source_url': f'https://www.youtube.com/watch?v={video_id}' if video_id else '',
            })

        page_token = data.get('nextPageToken', '')
        if not page_token:
            break

    return tracks


def _fetch_via_ytdlp(url):
    """Fetch playlist using yt-dlp (public playlists only)."""
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

        artist = entry.get('artist') or entry.get('creator') or entry.get('uploader') or ''
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


def run_youtube_import(operation_id):
    """Fetch a YouTube playlist and parse tracks. Runs in a background thread."""
    thread = threading.Thread(
        target=_youtube_worker,
        args=(operation_id,),
        daemon=True,
    )
    thread.start()


def _youtube_worker(operation_id):
    try:
        op = ImportOperation.objects.get(pk=operation_id)
        op.status = 'fetching'
        op.save()

        api_key = _get_api_key()
        playlist_id = _extract_playlist_id(op.url)

        # Try YouTube Data API first (supports private playlists), fallback to yt-dlp
        if api_key and playlist_id:
            try:
                tracks = _fetch_via_api(playlist_id, api_key)
            except Exception as api_err:
                logger.warning(f'YouTube API failed, falling back to yt-dlp: {api_err}')
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
        logger.exception(f'YouTube import failed for operation {operation_id}')
        try:
            op = ImportOperation.objects.get(pk=operation_id)
            op.status = 'failed'
            op.error_message = str(e)
            op.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()
