"""Bounded evidence from a caption with an explicit label, number and title."""
import json
import re
import threading
import time
from unicodedata import category

import requests

_LOCK = threading.Lock()
_NEXT = 0.0
_ORIGIN = 'https://api.discogs.com'


def tracklist_evidence(header):
    global _NEXT
    match = re.fullmatch(r'@([a-zA-Z0-9][a-zA-Z0-9_]{1,39}) +([0-9]{2,6}) +[-–—] +([^?]{1,100})', header)
    if not match or not _LOCK.acquire(blocking=False):
        return []
    try:
        if time.monotonic() < _NEXT:
            return []
        from core.views import get_config
        from wanted.services.preview import _same_name

        label, number, release_title = match.groups()
        label = label.rstrip('_').replace('_', ' ')
        # Social captions can append punctuation and decorative emoji.
        release_title = release_title.rstrip()
        while release_title and (release_title[-1] in '.!…\ufe0f' or category(release_title[-1]) == 'So'):
            release_title = release_title[:-1].rstrip()
        if not release_title:
            return []
        token = get_config('DISCOGS_PERSONAL_TOKEN')
        if not token:
            return []
        _NEXT = time.monotonic() + 1.1
        with requests.Session() as session:
            session.trust_env = False
            session.headers.update({'Authorization': f'Discogs token={token}',
                                    'User-Agent': 'OCDJ/2.0 (https://github.com/IvPalmer/ocdj)'})
            search = _json(session, '/database/search', {'type': 'release', 'release_title': release_title,
                                                         'label': label, 'per_page': 3})
            hits = search.get('results', [])
            if search.get('pagination', {}).get('items') != 1 or len(hits) != 1:
                return []
            release_id = hits[0]['id']
            if type(release_id) is not int or not 0 < release_id < 10**10:
                return []
            time.sleep(max(0, _NEXT - time.monotonic()))
            _NEXT = time.monotonic() + 1.1
            release = _json(session, f'/releases/{release_id}')
        if release.get('id') != release_id or not _same_name(release.get('title', ''), release_title):
            return []
        labels = [row for row in release.get('labels', []) if _same_name(row.get('name', ''), label)
                  and re.fullmatch(r'[A-Za-z0-9 ._-]{1,54}' + re.escape(number), row.get('catno', ''))
                  and re.search(r'[0-9]+$', row['catno'])[0] == number]
        if len(labels) != 1:
            return []
        records = []
        for track in release.get('tracklist', [])[:10]:
            if track.get('type_') != 'track':
                continue
            title = track.get('title', '').strip()
            artist = ', '.join(credit['name'] for credit in track.get('artists') or release.get('artists', []))
            if not artist or not title or max(len(artist), len(title)) > 500 or artist.casefold() == 'various':
                continue
            text = (f'Catalog: {labels[0]["catno"]}; https://www.discogs.com/release/{release_id}; '
                    f'{track.get("position", "")} {artist} — {title}; release tracklist; clip unverified')
            records.append({'source': 'caption', 'comment_id': '', 'text': text[:1100],
                            'candidate': {'artist': artist, 'title': title}})
        return records
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
        return []
    finally:
        _LOCK.release()


def _json(session, path, params=None):
    with session.get(_ORIGIN + path, params=params, timeout=(3, 5), allow_redirects=False,
                     stream=True) as response:
        if response.status_code != 200:
            return {}
        body = bytearray()
        deadline = time.monotonic() + 8
        for chunk in response.iter_content(8192):
            body.extend(chunk)
            if len(body) > 512 * 1024 or time.monotonic() > deadline:
                return {}
    return json.loads(body)
