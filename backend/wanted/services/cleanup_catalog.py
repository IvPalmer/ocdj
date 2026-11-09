"""Explicit iTunes candidate evidence for one saved receipt; no model access."""
from copy import deepcopy
from urllib.parse import urlsplit

import requests

from .cleanup_research import build_cleanup_proposal
from .preview import _clean, search_itunes_catalog


def _observations(payload):
    if not isinstance(payload, dict):
        raise ValueError('Invalid catalog envelope')
    rows = payload.get('results')
    count = payload.get('resultCount')
    if (not isinstance(rows, list) or type(count) is not int
            or count != len(rows) or not 0 <= count <= 3):
        raise ValueError('Invalid catalog result count')
    evidence = []
    for row in rows:
        if (not isinstance(row, dict) or row.get('wrapperType') != 'track'
                or row.get('kind') != 'song' or type(row.get('trackId')) is not int
                or row['trackId'] <= 0
                or any(not isinstance(row.get(key), str) or not row[key].strip()
                       or len(row[key]) > 700
                       for key in ('artistName', 'trackName', 'trackViewUrl'))):
            raise ValueError('Invalid catalog song')
        url = urlsplit(row['trackViewUrl'])
        if (url.scheme != 'https' or url.hostname not in ('music.apple.com', 'itunes.apple.com')
                or url.username or url.password or url.port not in (None, 443)):
            raise ValueError('Invalid catalog locator')
        evidence.append({
            'source_url': row['trackViewUrl'],
            'raw_title': f"{row['artistName']} - {row['trackName']}",
            # A catalog song hit is not evidence that this receipt is that recording.
            # Let #30 retain candidates but withhold artist suggestions.
            'granularity': 'unknown',
            'provider': 'itunes', 'provider_id': row['trackId'],
            'provider_payload': deepcopy(row),
            'identity_note': 'Catalog search hit; receipt recording/version identity unverified',
        })
    return evidence


def research_cleanup_catalog(receipt):
    """Validate before requesting; pass bounded, unselected hits to #30's evaluator."""
    proposal = build_cleanup_proposal(receipt, [])
    metadata = receipt['snapshot']['metadata']
    query = _clean(metadata['artist'], metadata['title'])
    lookup = {'provider': 'itunes', 'query': query, 'limit': 3,
              'status': 'not_queried', 'reason': 'missing_or_oversized_title_query'}
    if metadata['title'].strip() and len(query) <= 512:
        try:
            evidence = _observations(search_itunes_catalog(metadata['artist'], metadata['title']))
        except requests.RequestException:
            lookup.update(status='transport_failure', reason='request_failed_without_retry')
        except (ValueError, UnicodeError):
            lookup.update(status='invalid_response', reason='unusable_provider_response')
        else:
            proposal = build_cleanup_proposal(receipt, evidence)
            lookup.update(status='results' if evidence else 'no_result',
                          reason='bounded_search_not_exhaustive')
    proposal['lookup'] = lookup
    return proposal
