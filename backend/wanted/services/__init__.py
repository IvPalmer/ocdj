from .youtube import run_youtube_import
from .soundcloud import run_soundcloud_import
from .spotify import run_spotify_import, get_spotify_auth_url, handle_spotify_callback, check_spotify_status
from .discogs import run_discogs_import
from .bandcamp import run_bandcamp_import


PLAYLIST_NAME_KEYS = {
    'youtube': 'YOUTUBE_DEFAULT_PLAYLIST_NAME',
    'soundcloud': 'SC_DEFAULT_PLAYLIST_NAME',
    'spotify': 'SPOTIFY_DEFAULT_PLAYLIST_NAME',
}


def save_default_playlist_name(import_type, playlist_name):
    """Save the resolved playlist name to config if one was found."""
    key = PLAYLIST_NAME_KEYS.get(import_type)
    if not key or not playlist_name:
        return
    from core.models import Config
    Config.objects.update_or_create(key=key, defaults={'value': playlist_name})
