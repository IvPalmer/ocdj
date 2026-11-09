"""Bounded catalog evidence and conservative, version-preserving cleanup policy.

Use a verified ISRC or an explicit artist credit corroborated by the catalog.
Keep the complete mix/version text when repairing tags.
"""
import json
import re
import time
import unicodedata
import uuid

import requests

ISRC = re.compile(r'[A-Z]{2}[A-Z0-9]{3}[0-9]{7}\Z')


def clean_isrc(value):
    value = value.replace('-', '').upper() if isinstance(value, str) else ''
    return value if ISRC.fullmatch(value) else ''


def same_name(a, b):
    def normalize(value):
        return ' '.join(unicodedata.normalize('NFC', value).casefold().split())
    return isinstance(a, str) and isinstance(b, str) and normalize(a) == normalize(b)


def _get_json(url, params, *, missing=None):
    # Same bounded transport pattern as #34; never read netrc, env auth or proxies.
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, params=params, timeout=(5, 12), stream=True,
                         allow_redirects=False,
                         headers={'User-Agent': 'OCDJ/2.0 (https://github.com/IvPalmer/ocdj)'}) as response:
            if response.status_code == 404 and missing is not None:
                return missing
            if response.status_code != 200:
                raise requests.HTTPError('Catalog request failed')
            body = bytearray()
            deadline = time.monotonic() + 20
            for chunk in response.iter_content(chunk_size=8192):
                body.extend(chunk)
                if len(body) > 131072 or time.monotonic() > deadline:
                    raise ValueError('Catalog response exceeds bounds')
    return json.loads(body)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError('Invalid catalog text')
    return value


def collect_evidence(snapshot):
    isrc = clean_isrc(snapshot.get('isrc'))
    if isrc:
        # Called under the shared cleanup Huey lock. A cooldown, even on errors,
        # prevents this consumer from exceeding MusicBrainz's one request/second.
        try:
            payload = _get_json(f'https://musicbrainz.org/ws/2/isrc/{isrc}',
                                {'inc': 'artist-credits+isrcs', 'fmt': 'json'},
                                missing={'isrc': isrc, 'recordings': []})
        finally:
            time.sleep(1.1)
        if not isinstance(payload, dict) or clean_isrc(payload.get('isrc')) != isrc:
            raise ValueError('Invalid ISRC envelope')
        rows = payload.get('recordings')
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError('Invalid recordings')
        candidates = []
        for row in rows:
            recording_id = str(uuid.UUID(row['id']))
            credits = row['artist-credit']
            if not isinstance(credits, list) or not credits or len(credits) > 30:
                raise ValueError('Invalid credits')
            artist = ''.join(_text(c['name']) + c.get('joinphrase', '') for c in credits)
            isrcs = row.get('isrcs', [])
            disambiguation = row.get('disambiguation', '')
            if (not isinstance(isrcs, list) or len(isrcs) > 100
                    or any(not isinstance(code, str) or clean_isrc(code) != code for code in isrcs)
                    or not isinstance(disambiguation, str) or len(disambiguation) > 500):
                raise ValueError('Invalid recording identity')
            candidates.append({'provider': 'musicbrainz', 'id': recording_id,
                               'artist': _text(artist), 'title': _text(row['title']),
                               'isrc': isrc, 'isrcs': isrcs,
                               'disambiguation': disambiguation,
                               'url': f'https://musicbrainz.org/recording/{recording_id}'})
        return {'kind': 'isrc_lookup', 'isrc': isrc, 'candidates': candidates}

    title, artist = snapshot.get('title', ''), snapshot.get('artist', '')
    term = ' '.join(x.strip() for x in (artist, title) if x.strip())
    if not title.strip() or len(term) > 512:
        return {'kind': 'not_queried', 'candidates': []}
    try:
        payload = _get_json('https://itunes.apple.com/search',
                            {'term': term, 'entity': 'song', 'limit': 3})
    finally:
        # Apple's primary docs specify approximately 20 calls per minute.
        # The shared Huey lock plus cooldown bounds this cleanup consumer.
        time.sleep(3.1)
    if (not isinstance(payload, dict) or not isinstance(payload.get('results'), list)
            or type(payload.get('resultCount')) is not int
            or payload['resultCount'] != len(payload['results'])
            or len(payload['results']) > 3):
        raise ValueError('Invalid search envelope')
    candidates = []
    for row in payload['results']:
        if (row.get('wrapperType') != 'track' or row.get('kind') != 'song'
                or type(row.get('trackId')) is not int or row['trackId'] <= 0):
            raise ValueError('Invalid search result')
        candidates.append({'provider': 'itunes', 'id': row['trackId'],
                           'artist': _text(row['artistName']), 'title': _text(row['trackName'])})
    return {'kind': 'title_search', 'candidates': candidates}


def evaluate(snapshot, evidence):
    candidates = evidence.get('candidates', [])
    result = {'changes': {}, 'reason': 'ambiguous_or_unanchored'}
    if snapshot.get('protected'):
        result['reason'] = 'protected'
        return result
    if not candidates:
        result['reason'] = 'no_evidence'
        return result

    artist, title = snapshot.get('artist', ''), snapshot.get('title', '')
    kind = evidence.get('kind')
    if kind == 'isrc_lookup' and len(candidates) == 1:
        candidate = candidates[0]
        code = clean_isrc(snapshot.get('isrc'))
        # Catalog disambiguation may distinguish versions with identical titles.
        if (code and evidence.get('isrc') == code
                and candidate.get('provider') == 'musicbrainz'
                and candidate.get('isrc') == code
                and code in candidate.get('isrcs', [])
                and not candidate.get('disambiguation')):
            return verified_changes(result, artist, title, candidate, 'exact_isrc_and_full_title')

    if kind == 'title_search' and not artist.strip():
        # The search corroborates both tags explicitly present in the original
        # title. A title-only hit cannot supply an artist on its own.
        explicit = [row for row in candidates if row.get('provider') == 'itunes'
                    and has_artist_prefix(title, row)]
        if len(explicit) == 1:
            return verified_changes(result, artist, title, explicit[0], 'catalog_confirms_explicit_credit')
    return result


def has_artist_prefix(title, candidate):
    return same_name(title, f"{candidate['artist']} - {candidate['title']}")


def verified_changes(result, artist, title, candidate, reason):
    credit = candidate['artist']
    prefixed = has_artist_prefix(title, candidate)
    if (not credit or same_name(credit, 'Various Artists') or same_name(credit, '[unknown]')
            or not (prefixed or same_name(title, candidate['title']))):
        return result
    changes = {}
    if not same_name(artist, credit):
        changes['artist'] = credit
    if prefixed:
        changes['title'] = candidate['title']
    return {'changes': changes, 'reason': reason}


def evaluate_review(snapshot, evidence):
    """Protected historical hypotheses, never application authority.

    Search credit conflicts are review leads, never verified repairs. Preserve
    full titles: a remix mismatch cannot support a metadata delta.
    """
    assessed = []
    for candidate in evidence.get('candidates', []):
        full_title = (same_name(snapshot.get('title', ''), candidate.get('title', ''))
                      or has_artist_prefix(snapshot.get('title', ''), candidate))
        assessed.append({'provider': candidate.get('provider'), 'id': candidate.get('id'),
                         'assessment': 'full_title_only_identity_unverified' if full_title
                         else 'version_or_title_conflict'})
    # Reuse strict policy for anchored hypotheses but never expose its write
    # delta under "changes". Human edits retain all authority.
    hypothesis = evaluate({**snapshot, 'protected': False}, evidence)
    return {'changes': {}, 'proposed_changes': hypothesis['changes'],
            'reason': 'legacy_review_only', 'hypothesis_reason': hypothesis['reason'],
            'candidate_assessments': assessed}
