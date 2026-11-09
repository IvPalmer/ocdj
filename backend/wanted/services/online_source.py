"""Find a browser-playable recording without downloading any audio."""
import hashlib
import json
import logging
import re
import subprocess
from unicodedata import normalize

logger = logging.getLogger(__name__)
OFFICIAL_SUFFIX = re.compile(r'\s*[\[(](?:official (?:audio|video|music video)|lyric video)[\])]\s*$', re.I)


def recording_name(value):
    return ' '.join(re.findall(r'[^\W_]+', normalize('NFKC', value).casefold()))


def youtube_source(artist, title):
    try:
        result = subprocess.run(
            ['yt-dlp', '--ignore-config', '--flat-playlist', '--dump-single-json',
             '--no-warnings', f'ytsearch8:{artist} {title}'],
            capture_output=True, text=True, timeout=10, check=True,
        )
        entries = json.loads(result.stdout).get('entries') or []
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.info('Wanted YouTube lookup unavailable')
        return ''

    artist_name, track_name = recording_name(artist), recording_name(title)
    full_names = {f'{artist_name} {track_name}', f'{track_name} {artist_name}'}
    for entry in entries:
        if not entry:
            continue
        video_id = entry.get('id') or ''
        name = recording_name(OFFICIAL_SUFFIX.sub('', entry.get('title') or ''))
        channel = recording_name(entry.get('channel') or entry.get('uploader') or '')
        if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) and (
                name in full_names or
                (name == track_name and channel in {artist_name, f'{artist_name} topic'})):
            return f'https://www.youtube.com/watch?v={video_id}'
    return ''


def spotify_source(artist, title):
    import requests
    from spotipy.cache_handler import MemoryCacheHandler
    from spotipy.exceptions import SpotifyOauthError
    from spotipy.oauth2 import SpotifyClientCredentials
    from core.services.config import get_config

    client_id, secret = get_config('SPOTIFY_CLIENT_ID'), get_config('SPOTIFY_CLIENT_SECRET')
    if not client_id or not secret:
        return ''
    try:
        auth = SpotifyClientCredentials(client_id, secret, requests_timeout=3,
                                        cache_handler=MemoryCacheHandler())
        token = auth.get_access_token(as_dict=False)
        response = requests.get(
            'https://api.spotify.com/v1/search',
            headers={'Authorization': f'Bearer {token}'},
            params={'q': f'artist:{artist} track:{title}', 'type': 'track', 'limit': 5},
            timeout=3,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, SpotifyOauthError, ValueError):
        logger.info('Wanted Spotify lookup unavailable')
        return ''
    artist_name, track_name = recording_name(artist), recording_name(title)
    for result in results.get('tracks', {}).get('items', []):
        artists = [a['name'] for a in result.get('artists', [])]
        track_id = result.get('id') or ''
        if (recording_name(result.get('name') or '') == track_name
                and artist_name in {recording_name(a) for a in [*artists, ', '.join(artists)]}
                and re.fullmatch(r'[A-Za-z0-9]{22}', track_id)):
            return f'https://open.spotify.com/track/{track_id}'
    return ''


def find_online_source(artist, title, skip_youtube=False):
    from django.core.cache import cache

    artist, title = artist.strip(), title.strip()
    if not recording_name(artist) or not recording_name(title):
        return '', ''
    key = 'wanted-online:v1:' + hashlib.sha256(f'{artist}\0{title}\0{skip_youtube}'.encode()).hexdigest()
    if hit := cache.get(key):
        return hit
    for provider, lookup in (('youtube', youtube_source), ('spotify', spotify_source)):
        if provider == 'youtube' and skip_youtube:
            continue
        if url := lookup(artist, title):
            hit = (url, provider)
            cache.set(key, hit, timeout=3600)
            return hit
    return '', ''
