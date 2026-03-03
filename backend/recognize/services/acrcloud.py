"""ACRCloud audio recognition engine.

ACRCloud has 150M+ fingerprints in their shared music bucket (console API
shows stale 72M count from 2020, but recognition queries the live database). Uses HMAC-SHA1 signed
requests with audio samples sent as multipart/form-data.

API docs: https://docs.acrcloud.com/reference/identification-api
"""
import base64
import hashlib
import hmac
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
RATE_LIMIT_SECONDS = 0.5  # ACRCloud is much more lenient than Shazam

# ACRCloud error codes that mean credentials/config are wrong — no point retrying
AUTH_ERROR_CODES = {3001, 3002, 3003, 3014, 3015}  # invalid key, limit exceeded, etc.


class ACRCloudAuthError(Exception):
    """Raised when ACRCloud returns an auth/config error — fail fast."""
    pass


def _get_credentials():
    """Get ACRCloud credentials from Django config."""
    from core.views import get_config
    return {
        'access_key': get_config('ACRCLOUD_ACCESS_KEY'),
        'access_secret': get_config('ACRCLOUD_ACCESS_SECRET'),
        'host': get_config('ACRCLOUD_HOST', 'identify-eu-west-1.acrcloud.com'),
    }


def _sign_request(access_key, access_secret, timestamp):
    """Generate HMAC-SHA1 signature for ACRCloud API."""
    string_to_sign = f'POST\n/v1/identify\n{access_key}\naudio\n1\n{timestamp}'
    sign = base64.b64encode(
        hmac.new(
            access_secret.encode(),
            string_to_sign.encode(),
            hashlib.sha1,
        ).digest()
    ).decode()
    return sign


def recognize_file(file_path, access_key=None, access_secret=None, host=None):
    """Recognize a single audio file via ACRCloud.

    Args:
        file_path: Path to audio file (mp3, wav, etc.)
        access_key: ACRCloud access key (uses config if not provided)
        access_secret: ACRCloud access secret (uses config if not provided)
        host: ACRCloud host (uses config if not provided)

    Returns:
        dict with {title, artist, album, label, score, acrid, external_metadata} or None
    """
    if not access_key:
        creds = _get_credentials()
        access_key = creds['access_key']
        access_secret = creds['access_secret']
        host = creds['host']

    if not access_key or not access_secret:
        return None

    try:
        file_size = os.path.getsize(file_path)
        timestamp = str(int(time.time()))
        signature = _sign_request(access_key, access_secret, timestamp)

        with open(file_path, 'rb') as f:
            resp = requests.post(
                f'https://{host}/v1/identify',
                data={
                    'access_key': access_key,
                    'sample_bytes': file_size,
                    'timestamp': timestamp,
                    'signature': signature,
                    'data_type': 'audio',
                    'signature_version': '1',
                },
                files={'sample': f},
                timeout=REQUEST_TIMEOUT,
            )

        result = resp.json()
        status_code = result.get('status', {}).get('code', -1)

        if status_code == 0:
            # Success — extract first music match
            music = result.get('metadata', {}).get('music', [])
            if music:
                return _parse_music_result(music[0])
        elif status_code == 1001:
            # No result found
            return None
        elif status_code in AUTH_ERROR_CODES:
            msg = result.get('status', {}).get('msg', 'Unknown error')
            raise ACRCloudAuthError(f'ACRCloud config error {status_code}: {msg}')
        else:
            msg = result.get('status', {}).get('msg', 'Unknown error')
            logger.debug(f'ACRCloud status {status_code}: {msg} for {file_path}')
            return None

    except ACRCloudAuthError:
        raise  # Let auth errors propagate — caller should abort
    except Exception as e:
        logger.warning(f'ACRCloud recognition failed for {file_path}: {e}')
        return None


def recognize_segments(segments, on_progress=None, access_key=None, access_secret=None, host=None):
    """Recognize all audio segments via ACRCloud.

    Args:
        segments: List of (segment_file_path, start_seconds) tuples
        on_progress: Callback(done, total) for progress updates
        access_key/access_secret/host: ACRCloud credentials (uses config if not provided)

    Returns:
        List of {start_sec, track: {...} or None, engine: 'acrcloud', confidence_score: float}
    """
    if not access_key:
        creds = _get_credentials()
        access_key = creds['access_key']
        access_secret = creds['access_secret']
        host = creds['host']

    if not access_key or not access_secret:
        logger.warning('ACRCloud credentials not configured')
        return []

    results = []
    total = len(segments)

    for i, (seg_path, start_sec) in enumerate(segments):
        try:
            track_info = recognize_file(seg_path, access_key, access_secret, host)
        except ACRCloudAuthError as e:
            # Auth/config error — abort immediately instead of wasting calls
            logger.error(f'ACRCloud auth error on segment {i+1}/{total}, aborting: {e}')
            results.append({
                'start_sec': start_sec,
                'track': None,
                'engine': 'acrcloud',
                'confidence_score': 0.0,
            })
            if on_progress:
                on_progress(total, total)
            break

        confidence_score = 0.0
        if track_info:
            confidence_score = track_info.pop('score', 0) / 100.0

        results.append({
            'start_sec': start_sec,
            'track': track_info,
            'engine': 'acrcloud',
            'confidence_score': confidence_score,
        })

        if on_progress:
            on_progress(i + 1, total)

        # Rate limit (skip after last segment)
        if i < total - 1:
            time.sleep(RATE_LIMIT_SECONDS)

    return results


def _parse_music_result(music):
    """Parse ACRCloud music result into our track format."""
    artists = music.get('artists', [])
    artist_name = artists[0].get('name', '') if artists else ''

    album = music.get('album', {})
    album_name = album.get('name', '')

    label = music.get('label', '')

    # Extract external URLs
    external = music.get('external_metadata', {})
    spotify_id = ''
    if 'spotify' in external:
        sp = external['spotify'].get('track', {})
        spotify_id = sp.get('id', '')

    return {
        'title': music.get('title', ''),
        'artist': artist_name,
        'key': music.get('acrid', ''),
        'album': album_name,
        'label': label,
        'shazam_url': '',
        'apple_music_url': '',
        'spotify_id': spotify_id,
        'confidence_score': music.get('score', 0),
        'score': music.get('score', 0),
    }
