"""User-selected release suggestions. Never write track tags here."""
import logging
import re

import requests

from core.services.config import get_config

logger = logging.getLogger(__name__)


def _identity(value):
    return ' '.join(str(value or '').casefold().split())


def _text(value, limit=500):
    return str(value or '')[:limit].strip()


def _year(value):
    match = re.match(r'^\d{4}', str(value or ''))
    return match[0] if match else ''


def _discogs(artist, title, token):
    headers = {'Authorization': f'Discogs token={token}', 'User-Agent': 'OCDJ/2.0'}
    base = 'https://api.discogs.com'
    with requests.Session() as session:
        response = session.get(base + '/database/search', params={
            'artist': artist, 'track': title, 'type': 'release', 'per_page': 3,
        }, headers=headers, timeout=4)
        response.raise_for_status()
        for match in response.json().get('results', [])[:3]:
            release_id = match.get('id')
            if type(release_id) is not int or release_id < 1:
                continue
            response = session.get(f'{base}/releases/{release_id}', headers=headers, timeout=4)
            if not response.ok:
                continue
            release = response.json()
            for track in release.get('tracklist', []):
                artists = track.get('artists') or release.get('artists') or []
                names = [_identity(re.sub(r' \(\d+\)$', '', entry.get('name', ''))) for entry in artists]
                if _identity(track.get('title', '')) != _identity(title) or _identity(artist) not in names + [', '.join(names)]:
                    continue
                label = (release.get('labels') or [{}])[0]
                name = re.sub(r' \(\d+\)$', '', _text(label.get('name'), 255))
                catno = _text(label.get('catno'), 100)
                position = _text(track.get('position'), 20)
                yield {
                    'album': _text(release.get('title')),
                    'label': '' if name.casefold().startswith('not on label') else name,
                    'catalog_number': '' if catno.casefold() in ('none', 'n/a') else catno,
                    'year': _year(release.get('year')),
                    'track_number': position if position.isascii() and position.isdecimal() else '',
                    'provider': 'Discogs',
                    'url': f'https://www.discogs.com/release/{release_id}',
                }
                break


def _musicbrainz(artist, title):
    def escape(value):
        return value.replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"')

    response = requests.get('https://musicbrainz.org/ws/2/recording', params={
        'query': f'recording:"{escape(title)}" AND artist:"{escape(artist)}"', 'limit': 5, 'fmt': 'json',
    }, headers={'User-Agent': 'OCDJ/2.0 (https://github.com/IvPalmer/ocdj)'}, timeout=6)
    response.raise_for_status()
    for recording in response.json().get('recordings', []):
        credit = ''.join(part.get('name', '') + part.get('joinphrase', '')
                         for part in recording.get('artist-credit', []))
        if _identity(recording.get('title', '')) != _identity(title) or _identity(credit) != _identity(artist):
            continue
        for release in recording.get('releases', [])[:8]:
            rid = release.get('id', '')
            if not re.fullmatch(r'[0-9a-f-]{36}', rid):
                continue
            yield {'album': _text(release.get('title')), 'label': '', 'catalog_number': '',
                   'year': _year(release.get('date')), 'track_number': '',
                   'provider': 'MusicBrainz', 'url': f'https://musicbrainz.org/release/{rid}'}


def find_releases(artist, title):
    candidates = {}
    token = get_config('DISCOGS_PERSONAL_TOKEN')
    providers = [_musicbrainz]
    if token:
        providers.insert(0, lambda artist, title: _discogs(artist, title, token))
    errors = 0
    for provider in providers:
        try:
            for result in provider(artist, title):
                if result['album']:
                    candidates[result['url']] = result
                if len(candidates) >= 10:
                    return list(candidates.values())
        except (requests.RequestException, ValueError, TypeError):
            errors += 1
            logger.warning('Organize release lookup provider unavailable')
    if errors == len(providers) and not candidates:
        raise requests.RequestException('Release catalogs unavailable')
    return list(candidates.values())
