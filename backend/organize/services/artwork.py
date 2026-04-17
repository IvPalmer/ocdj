import logging

import requests

logger = logging.getLogger(__name__)


def fetch_artwork(artist, title, label='', catalog_number=''):
    """Try to fetch cover art. Discogs first, then Spotify. Returns bytes or None."""
    image_bytes = _fetch_from_discogs(artist, title, label, catalog_number)
    if image_bytes:
        return image_bytes

    image_bytes = _fetch_from_spotify(artist, title)
    if image_bytes:
        return image_bytes

    return None


def _fetch_from_discogs(artist, title, label='', catalog_number=''):
    """Fetch artwork from Discogs."""
    try:
        from core.views import get_config
        token = get_config('DISCOGS_PERSONAL_TOKEN')
        if not token:
            return None

        import discogs_client
        d = discogs_client.Client('OCDJ/2.0', user_token=token)

        if catalog_number:
            results = d.search(catno=catalog_number, type='release')
        elif artist and title:
            results = d.search(f'{artist} {title}', type='release')
        else:
            return None

        # discogs_client paginated lists don't support Python slice syntax.
        from itertools import islice
        for result in islice(results, 3):
            if hasattr(result, 'images') and result.images:
                img_url = result.images[0].get('uri') or result.images[0].get('resource_url')
                if img_url:
                    resp = requests.get(img_url, timeout=15)
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        return resp.content
    except Exception as e:
        logger.warning(f"Discogs artwork fetch failed: {e}")

    return None


def _fetch_from_spotify(artist, title):
    """Fetch album artwork from Spotify search."""
    try:
        from core.views import get_config
        client_id = get_config('SPOTIFY_CLIENT_ID')
        client_secret = get_config('SPOTIFY_CLIENT_SECRET')
        if not client_id or not client_secret:
            return None

        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
        ))

        query = f'{artist} {title}' if artist else title
        results = sp.search(q=query, type='track', limit=3)

        for track in results.get('tracks', {}).get('items', []):
            album = track.get('album', {})
            images = album.get('images', [])
            if images:
                # Get the largest image
                img_url = images[0].get('url')
                if img_url:
                    resp = requests.get(img_url, timeout=15)
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        return resp.content
    except Exception as e:
        logger.warning(f"Spotify artwork fetch failed: {e}")

    return None


def embed_artwork(filepath, image_bytes):
    """Embed artwork into an audio file."""
    import os
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import ID3, APIC

    ext = os.path.splitext(filepath)[1].lower()

    try:
        if ext == '.flac':
            flac = FLAC(filepath)
            pic = Picture()
            pic.type = 3  # Cover (front)
            pic.mime = 'image/jpeg'
            pic.desc = 'Cover'
            pic.data = image_bytes
            flac.clear_pictures()
            flac.add_picture(pic)
            flac.save()

        elif ext in ('.mp3', '.aiff', '.aif'):
            audio = mutagen.File(filepath)
            if audio.tags is None:
                audio.add_tags()

            audio.tags.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,  # Cover (front)
                desc='Cover',
                data=image_bytes,
            ))
            audio.save()

        else:
            logger.info(f"Artwork embedding not supported for {ext}")
    except Exception as e:
        logger.warning(f"Failed to embed artwork in {filepath}: {e}")
