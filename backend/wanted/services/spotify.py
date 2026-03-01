import logging
import os
import re
import threading

from django import db
from django.conf import settings

from wanted.models import ImportOperation
from .dedup import check_duplicates

logger = logging.getLogger(__name__)

SPOTIFY_CLIENT_ID = None
SPOTIFY_CLIENT_SECRET = None
SPOTIFY_REDIRECT_URI = None
CACHE_PATH = None


def _get_config():
    from core.views import get_config
    return {
        'client_id': get_config('SPOTIFY_CLIENT_ID'),
        'client_secret': get_config('SPOTIFY_CLIENT_SECRET'),
        'redirect_uri': get_config('SPOTIFY_REDIRECT_URI') or 'http://localhost:8002/api/wanted/import/spotify/callback/',
        'cache_path': os.path.join(settings.BASE_DIR, '.spotify_cache'),
    }


def _get_sp():
    """Get an authenticated Spotify client."""
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    config = _get_config()
    auth_manager = SpotifyOAuth(
        client_id=config['client_id'],
        client_secret=config['client_secret'],
        redirect_uri=config['redirect_uri'],
        scope='playlist-read-private playlist-read-collaborative',
        cache_handler=spotipy.CacheFileHandler(cache_path=config['cache_path']),
    )
    return spotipy.Spotify(auth_manager=auth_manager), auth_manager


def get_spotify_auth_url():
    """Return the Spotify OAuth authorization URL."""
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    config = _get_config()
    auth_manager = SpotifyOAuth(
        client_id=config['client_id'],
        client_secret=config['client_secret'],
        redirect_uri=config['redirect_uri'],
        scope='playlist-read-private playlist-read-collaborative',
        cache_handler=spotipy.CacheFileHandler(cache_path=config['cache_path']),
    )
    return auth_manager.get_authorize_url()


def handle_spotify_callback(code):
    """Exchange authorization code for token."""
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    config = _get_config()
    auth_manager = SpotifyOAuth(
        client_id=config['client_id'],
        client_secret=config['client_secret'],
        redirect_uri=config['redirect_uri'],
        scope='playlist-read-private playlist-read-collaborative',
        cache_handler=spotipy.CacheFileHandler(cache_path=config['cache_path']),
    )
    auth_manager.get_access_token(code)
    return True


def check_spotify_status():
    """Check if we have a valid Spotify token."""
    config = _get_config()
    if not config['client_id'] or not config['client_secret']:
        return {'configured': False, 'connected': False}

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        auth_manager = SpotifyOAuth(
            client_id=config['client_id'],
            client_secret=config['client_secret'],
            redirect_uri=config['redirect_uri'],
            scope='playlist-read-private playlist-read-collaborative',
            cache_handler=spotipy.CacheFileHandler(cache_path=config['cache_path']),
        )
        token_info = auth_manager.get_cached_token()
        if token_info:
            return {'configured': True, 'connected': True}
        return {'configured': True, 'connected': False}
    except Exception:
        return {'configured': True, 'connected': False}


def _extract_playlist_id(url):
    """Extract Spotify playlist ID from URL."""
    match = re.search(r'playlist[/:]([a-zA-Z0-9]+)', url)
    return match.group(1) if match else None


def run_spotify_import(operation_id):
    """Fetch a Spotify playlist and parse tracks. Runs in a background thread."""
    thread = threading.Thread(
        target=_spotify_worker,
        args=(operation_id,),
        daemon=True,
    )
    thread.start()


def _spotify_worker(operation_id):
    try:
        op = ImportOperation.objects.get(pk=operation_id)
        op.status = 'fetching'
        op.save()

        sp, _ = _get_sp()
        playlist_id = _extract_playlist_id(op.url)
        if not playlist_id:
            raise ValueError(f'Could not extract playlist ID from URL: {op.url}')

        # Fetch playlist name
        try:
            playlist_info = sp.playlist(playlist_id, fields='name')
            op.playlist_name = playlist_info.get('name', '')
            op.save()
        except Exception:
            pass

        tracks = []
        results = sp.playlist_items(playlist_id, limit=100)

        while True:
            for item in results.get('items', []):
                track_obj = item.get('track')
                if not track_obj:
                    continue

                artists = ', '.join(a['name'] for a in track_obj.get('artists', []))
                title = track_obj.get('name', '')
                album = track_obj.get('album', {})

                tracks.append({
                    'artist': artists,
                    'title': title,
                    'release_name': album.get('name', ''),
                    'label': album.get('label', ''),
                    'raw_title': f"{artists} - {title}",
                    'source_url': track_obj.get('external_urls', {}).get('spotify', ''),
                })

            if results.get('next'):
                results = sp.next(results)
            else:
                break

        tracks = check_duplicates(tracks)

        duplicates = sum(1 for t in tracks if t.get('is_duplicate'))
        op.preview_data = tracks
        op.total_found = len(tracks)
        op.duplicates_found = duplicates
        op.status = 'previewing'
        op.save()

        from . import save_default_playlist_name
        save_default_playlist_name('spotify', op.playlist_name)

    except Exception as e:
        logger.exception(f'Spotify import failed for operation {operation_id}')
        try:
            op = ImportOperation.objects.get(pk=operation_id)
            op.status = 'failed'
            op.error_message = str(e)
            op.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()
