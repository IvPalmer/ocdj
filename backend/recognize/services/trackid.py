"""TrackID.net API client for mix recognition.

TrackID.net uses ACRCloud with 150M+ fingerprints — far superior to Shazam
for underground/electronic music. We use their public API to fetch existing
tracklists, and their private API to submit new mixes for processing.

Auth:
  - Public endpoints: no auth required (read-only)
  - Private endpoints: Bearer token from browser login (stored in settings)
  - Integration endpoints: clientId:clientSecret (separate API credentials)
"""
import logging
import time
from urllib.parse import urlparse, urljoin

import requests

logger = logging.getLogger(__name__)

BASE_URL = 'https://trackid.net/api'
REQUEST_TIMEOUT = 15


def lookup_by_url(url):
    """Check if a mix URL has already been processed on TrackID.net.

    Args:
        url: SoundCloud/YouTube/Mixcloud URL

    Returns:
        dict with {slug, title, tracklist, duration_seconds, status} or None
    """
    try:
        resp = requests.get(
            f'{BASE_URL}/public/audiostreams',
            params={'url': url},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        streams = data.get('result', {}).get('audiostreams', [])
        if not streams:
            return None

        # Use the first match
        stream = streams[0]
        return _fetch_stream_detail(stream['slug'])

    except Exception as e:
        logger.warning(f'TrackID lookup failed for {url}: {e}')
        return None


def _fetch_stream_detail(slug):
    """Fetch full audiostream detail including tracklist."""
    try:
        resp = requests.get(
            f'{BASE_URL}/public/audiostreams/{slug}',
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get('result', {})
        if not result:
            return None

        # Collect all tracks from all detection processes
        all_tracks = []
        for dp in result.get('detectionProcesses', []):
            for t in dp.get('detectionProcessMusicTracks', []):
                all_tracks.append(t)

        if not all_tracks:
            # Stream exists but no tracks yet (still processing?)
            status_val = result.get('status', 0)
            return {
                'slug': slug,
                'title': result.get('title', ''),
                'tracklist': [],
                'duration_seconds': _parse_duration(result.get('duration', '')),
                'trackid_status': 'processing' if status_val < 3 else 'empty',
            }

        # Sort by start time and convert to our format
        all_tracks.sort(key=lambda x: x.get('startTime', '00:00:00'))
        tracklist = _convert_tracklist(all_tracks)

        return {
            'slug': slug,
            'title': result.get('title', ''),
            'tracklist': tracklist,
            'duration_seconds': _parse_duration(result.get('duration', '')),
            'trackid_status': 'completed',
        }

    except Exception as e:
        logger.warning(f'TrackID detail fetch failed for {slug}: {e}')
        return None


def submit_url(url, token=None):
    """Submit a mix URL to TrackID.net for processing.

    Args:
        url: SoundCloud/YouTube/Mixcloud URL
        token: TrackID.net auth token (session token from browser login)

    Returns:
        dict with {slug, status} or None
    """
    if not token:
        from core.views import get_config
        token = get_config('TRACKID_TOKEN')
    if not token:
        logger.info('No TrackID token configured, skipping submission')
        return None

    try:
        resp = requests.post(
            f'{BASE_URL}/private/audiostreams',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            json={'url': url},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 401:
            logger.warning('TrackID auth failed — token may be expired')
            return None
        resp.raise_for_status()
        data = resp.json()
        result = data.get('result', {})
        return {
            'slug': result.get('slug', ''),
            'status': 'submitted',
        }
    except Exception as e:
        logger.warning(f'TrackID submission failed for {url}: {e}')
        return None


def poll_until_ready(slug, timeout_minutes=90, poll_interval=60):
    """Poll TrackID.net until processing is complete.

    Args:
        slug: audiostream slug
        timeout_minutes: max time to wait
        poll_interval: seconds between checks

    Returns:
        tracklist or None if timeout/error
    """
    deadline = time.time() + timeout_minutes * 60

    while time.time() < deadline:
        result = _fetch_stream_detail(slug)
        if not result:
            return None
        if result['trackid_status'] == 'completed' and result['tracklist']:
            return result
        logger.debug(f'TrackID still processing {slug}, waiting {poll_interval}s')
        time.sleep(poll_interval)

    logger.warning(f'TrackID processing timed out for {slug} after {timeout_minutes}m')
    return None


def _convert_tracklist(trackid_tracks):
    """Convert TrackID.net track format to our internal tracklist format."""
    tracklist = []
    seen = {}  # key -> last end_sec for dedup

    for t in trackid_tracks:
        artist = t.get('artist', '')
        title = t.get('title', '')
        key = f'{artist}:{title}'.lower()

        start_sec = _parse_time(t.get('startTime', '00:00:00'))
        end_sec = _parse_time(t.get('endTime', '00:00:00'))

        # Dedup — TrackID sometimes returns duplicates from reprocessing
        if key in seen:
            # Only skip if within 120s of previous occurrence
            prev_end = seen[key]
            if abs(start_sec - prev_end) < 120:
                continue
        seen[key] = end_sec

        tracklist.append({
            'artist': artist,
            'title': title,
            'album': '',
            'label': t.get('label', ''),
            'timestamp_start': start_sec,
            'timestamp_end': end_sec,
            'shazam_url': '',
            'apple_music_url': '',
            'confidence': 'verified',
            'confidence_score': 0.95,
            'segment_count': 0,
            'engines': ['trackid'],
            'in_description': False,
        })

    return tracklist


def _parse_time(time_str):
    """Parse HH:MM:SS or MM:SS time string to seconds."""
    if not time_str:
        return 0
    parts = time_str.split(':')
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + int(float(s))
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + int(float(s))
        return int(float(time_str))
    except (ValueError, TypeError):
        return 0


def _parse_duration(duration_str):
    """Parse TrackID duration format (HH:MM:SS.fffffff) to seconds."""
    if not duration_str:
        return 0
    # Strip fractional seconds
    if '.' in duration_str:
        duration_str = duration_str.split('.')[0]
    return _parse_time(duration_str)
