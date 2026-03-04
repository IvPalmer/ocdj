import json
import logging
import re
import threading

import requests
from bs4 import BeautifulSoup
from django import db

from wanted.models import ImportOperation
from .dedup import check_duplicates

logger = logging.getLogger(__name__)

USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def _get_page(url):
    """Fetch a Bandcamp page with a browser-like User-Agent."""
    resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def _extract_tralbum_data(html):
    """Extract the TrAlbum JSON data embedded in a Bandcamp page."""
    # Bandcamp embeds track data as a JSON blob assigned to TralbumData
    match = re.search(r'var\s+TralbumData\s*=\s*(\{.+?\})\s*;', html, re.DOTALL)
    if match:
        # The JSON blob has some JS-only syntax (unquoted keys sometimes), but
        # Bandcamp typically outputs valid JSON here. Clean trailing commas.
        raw = match.group(1)
        # Remove JS comments
        raw = re.sub(r'//[^\n]*', '', raw)
        # Remove trailing commas before } or ]
        raw = re.sub(r',\s*([}\]])', r'\1', raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    # Fallback: look for data-tralbum attribute
    soup = BeautifulSoup(html, 'html.parser')
    el = soup.find(attrs={'data-tralbum': True})
    if el:
        try:
            return json.loads(el['data-tralbum'])
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def _extract_page_data(html):
    """Extract page data JSON (used on artist/label pages)."""
    match = re.search(r'data-initial-values="([^"]*)"', html)
    if match:
        try:
            return json.loads(match.group(1).replace('&quot;', '"'))
        except json.JSONDecodeError:
            pass
    return None


def _parse_album_page(url):
    """Parse a Bandcamp album page and return track list."""
    html = _get_page(url)
    data = _extract_tralbum_data(html)

    soup = BeautifulSoup(html, 'html.parser')

    # Get artist and album from page if not in tralbum data
    artist = ''
    album = ''
    label = ''

    if data:
        artist = data.get('artist', '')
        album = data.get('current', {}).get('title', '')
        label = data.get('current', {}).get('label', '') or ''

    # Fallback to meta tags
    if not artist:
        tag = soup.find('meta', {'name': 'title'})
        if tag:
            content = tag.get('content', '')
            if ' | ' in content:
                artist = content.split(' | ')[-1].strip()

    if not artist:
        band_name = soup.find(id='band-name-location')
        if band_name:
            name_el = band_name.find(class_='title')
            if name_el:
                artist = name_el.get_text(strip=True)

    tracks = []

    if data and data.get('trackinfo'):
        for t in data['trackinfo']:
            tracks.append({
                'artist': artist,
                'title': t.get('title', ''),
                'release_name': album,
                'label': label,
                'raw_title': f"{artist} - {t.get('title', '')}",
                'source_url': url,
            })
    elif data and data.get('current'):
        # Single track page
        tracks.append({
            'artist': artist,
            'title': data['current'].get('title', ''),
            'release_name': album,
            'label': label,
            'raw_title': f"{artist} - {data['current'].get('title', '')}",
            'source_url': url,
        })

    if not tracks:
        # Try parsing track list from HTML
        track_rows = soup.select('.track_list .track-title') or soup.select('.track_row_view .title span')
        for row in track_rows:
            title = row.get_text(strip=True)
            if title:
                tracks.append({
                    'artist': artist,
                    'title': title,
                    'release_name': album,
                    'label': label,
                    'raw_title': f"{artist} - {title}",
                    'source_url': url,
                })

    return tracks, artist, album


def _parse_artist_or_label_page(url):
    """Parse an artist or label discography page."""
    html = _get_page(url)
    soup = BeautifulSoup(html, 'html.parser')

    artist = ''
    band_name = soup.find(id='band-name-location')
    if band_name:
        name_el = band_name.find(class_='title')
        if name_el:
            artist = name_el.get_text(strip=True)

    # Find all album/track links on the page
    music_grid = soup.select('#music-grid .music-grid-item a') or soup.select('.music-grid a')

    if not music_grid:
        # Try the older layout
        music_grid = soup.select('#discography .trackTitle a') or soup.select('.leftMiddleColumns a[href*="/album/"]')

    # Also check for ol#music-grid li items
    if not music_grid:
        items = soup.select('ol#music-grid li a')
        music_grid = items

    release_urls = []
    from urllib.parse import urljoin
    for link in music_grid:
        href = link.get('href', '')
        if '/album/' in href or '/track/' in href:
            full_url = urljoin(url, href)
            if full_url not in release_urls:
                release_urls.append(full_url)

    # Fetch each release page for track details
    tracks = []
    for release_url in release_urls:
        try:
            release_tracks, rel_artist, album = _parse_album_page(release_url)
            for t in release_tracks:
                if not t['artist']:
                    t['artist'] = artist
                    t['raw_title'] = f"{artist} - {t['title']}"
            tracks.extend(release_tracks)
        except Exception as e:
            logger.warning(f'Failed to fetch Bandcamp release {release_url}: {e}')

    return tracks, artist


def _parse_wishlist_or_collection(url):
    """Parse a Bandcamp wishlist or collection page."""
    html = _get_page(url)
    soup = BeautifulSoup(html, 'html.parser')

    # Wishlist/collection data is in a JSON blob
    # Look for the collection data in pagedata
    fan_data = None
    for script in soup.find_all('script'):
        text = script.string or ''
        if 'item_cache' in text or 'wishlist_data' in text or 'collection_data' in text:
            # Try to extract JSON from the data blob
            match = re.search(r'data-blob="([^"]*)"', str(soup))
            if match:
                try:
                    fan_data = json.loads(match.group(1).replace('&quot;', '"').replace('&amp;', '&'))
                except json.JSONDecodeError:
                    pass

    # Also try data-blob attribute on body or page element
    if not fan_data:
        blob_el = soup.find(attrs={'data-blob': True})
        if blob_el:
            try:
                fan_data = json.loads(blob_el['data-blob'])
            except (json.JSONDecodeError, KeyError):
                pass

    tracks = []

    if fan_data:
        item_cache = fan_data.get('item_cache', {})
        # item_cache has collection or wishlist items
        for key, item in (item_cache.get('collection', {}) or item_cache).items():
            if isinstance(item, dict):
                artist = item.get('band_name', '')
                title = item.get('album_title', '') or item.get('item_title', '')
                item_url = item.get('item_url', '')

                tracks.append({
                    'artist': artist,
                    'title': title,
                    'release_name': title,
                    'label': '',
                    'raw_title': f"{artist} - {title}",
                    'source_url': item_url,
                })

    # Fallback: parse wishlist items from HTML
    if not tracks:
        items = soup.select('.collection-item-container') or soup.select('.wishlist-item')
        for item in items:
            artist_el = item.select_one('.collection-item-artist') or item.select_one('.item-artist')
            title_el = item.select_one('.collection-item-title') or item.select_one('.item-title')
            link_el = item.select_one('a[href]')

            artist_text = artist_el.get_text(strip=True) if artist_el else ''
            # Clean "by " prefix
            artist_text = re.sub(r'^by\s+', '', artist_text)
            title_text = title_el.get_text(strip=True) if title_el else ''
            item_url = link_el.get('href', '') if link_el else ''

            if artist_text or title_text:
                tracks.append({
                    'artist': artist_text,
                    'title': title_text,
                    'release_name': title_text,
                    'label': '',
                    'raw_title': f"{artist_text} - {title_text}",
                    'source_url': item_url,
                })

    return tracks


def _classify_url(url):
    """Classify a Bandcamp URL type."""
    from urllib.parse import urlparse
    parsed = urlparse(url)

    path = parsed.path.rstrip('/')

    if '/album/' in path:
        return 'album'
    if '/track/' in path:
        return 'track'
    if '/wishlist' in path or '/collection' in path:
        return 'wishlist'
    if path == '/music' or path == '':
        return 'artist'
    return 'artist'


def fetch_bandcamp(url):
    """Fetch tracks from a Bandcamp URL. Returns (tracks, page_name)."""
    url_type = _classify_url(url)

    if url_type in ('album', 'track'):
        tracks, artist, album = _parse_album_page(url)
        page_name = f"{artist} - {album}" if artist and album else (album or artist or 'Bandcamp Import')
        return tracks, page_name

    if url_type == 'wishlist':
        tracks = _parse_wishlist_or_collection(url)
        return tracks, 'Bandcamp Wishlist'

    # artist or label page
    tracks, artist = _parse_artist_or_label_page(url)
    page_name = f"{artist} discography" if artist else 'Bandcamp Import'
    return tracks, page_name


def run_bandcamp_import(operation_id):
    """Fetch a Bandcamp page and parse tracks. Runs in a background thread."""
    thread = threading.Thread(
        target=_bandcamp_worker,
        args=(operation_id,),
        daemon=True,
    )
    thread.start()


def _bandcamp_worker(operation_id):
    try:
        op = ImportOperation.objects.get(pk=operation_id)
        op.status = 'fetching'
        op.save()

        tracks, page_name = fetch_bandcamp(op.url)

        op.playlist_name = page_name
        tracks = check_duplicates(tracks)

        duplicates = sum(1 for t in tracks if t.get('is_duplicate'))
        op.preview_data = tracks
        op.total_found = len(tracks)
        op.duplicates_found = duplicates
        op.status = 'previewing'
        op.save()

    except Exception as e:
        logger.exception(f'Bandcamp import failed for operation {operation_id}')
        try:
            op = ImportOperation.objects.get(pk=operation_id)
            op.status = 'failed'
            op.error_message = str(e)
            op.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()
