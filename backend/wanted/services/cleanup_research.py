"""Explicit, offline source-title hypotheses. Never reads or writes a subject."""
from copy import deepcopy
from urllib.parse import urlsplit
from uuid import UUID

from .parsers import parse_video_title

_METADATA_FIELDS = ('artist', 'title', 'release_name', 'catalog_number', 'label')
_GRANULARITIES = ('unknown', 'release', 'recording')


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _validate(receipt, evidence):
    if not isinstance(receipt, dict) or not isinstance(receipt.get('snapshot'), dict):
        raise ValueError('Expected a receipt envelope with snapshot')
    try:
        UUID(receipt['event_id'])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError('Expected a UUID event_id') from None
    snapshot = receipt['snapshot']
    metadata = snapshot.get('metadata')
    provenance = snapshot.get('provenance')
    if (type(snapshot.get('schema_version')) is not int or snapshot['schema_version'] != 1
            or not _integer(snapshot.get('wanted_id'), 1)
            or not isinstance(metadata, dict)
            or any(not isinstance(metadata.get(field), str) for field in _METADATA_FIELDS)
            or not isinstance(provenance, dict)
            or any(not isinstance(provenance.get(field), str) for field in ('artist', 'title'))
            or snapshot.get('granularity') not in _GRANULARITIES
            or 'source_id' not in snapshot
            or (snapshot['source_id'] is not None and not _integer(snapshot['source_id'], 1))):
        raise ValueError('Invalid schema-v1 snapshot')
    for field in ('metadata_revision', 'protection_revision'):
        if field not in snapshot or (snapshot[field] is not None and not _integer(snapshot[field])):
            raise ValueError('Invalid snapshot revision')
    if not isinstance(evidence, list) or len(evidence) > 20:
        raise ValueError('Expected a list of at most 20 source observations')
    for observation in evidence:
        if (not isinstance(observation, dict)
                or not isinstance(observation.get('raw_title'), str)
                or len(observation['raw_title']) > 2000
                or observation.get('granularity') not in _GRANULARITIES
                or not isinstance(observation.get('source_url'), str)):
            raise ValueError('Invalid source observation')
        try:
            url = urlsplit(observation['source_url'])
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                raise ValueError
        except ValueError:
            raise ValueError('Expected an HTTP(S) source URL without credentials') from None


def build_cleanup_proposal(receipt, evidence):
    """Return a deterministic review artifact from one retained receipt + evidence.

    Source granularity is caller-declared and recording identity stays unverified.
    Only a blank artist with an exact full-title anchor may receive a suggestion.
    No result is authority to apply; nonempty values and inputs are never mutated.
    """
    _validate(receipt, evidence)
    snapshot = receipt['snapshot']
    metadata = snapshot['metadata']
    candidates = []
    reasons = set()
    identities = set()
    for index, observation in enumerate(evidence):
        parsed = parse_video_title(observation['raw_title'])
        artist, title = parsed['artist'], parsed['title']
        rejected = []
        if observation['granularity'] != 'recording':
            rejected.append('source_not_recording')
        if not artist or not title:
            rejected.append('incomplete_parse')
        if f'{artist} - {title}' != observation['raw_title'].strip():
            rejected.append('parser_changed_source_text')
        if not metadata['title'].strip():
            rejected.append('missing_title_anchor')
        elif title.strip().casefold() != metadata['title'].strip().casefold():
            rejected.append('title_mismatch')
        if metadata['artist'].strip() and artist.casefold() != metadata['artist'].strip().casefold():
            rejected.append('artist_mismatch')
        candidates.append({'artist': artist, 'title': title, 'evidence_index': index,
                           'reasons': rejected})
        reasons.update(rejected)
        if artist and title:
            identities.add((artist.casefold(), title.casefold()))
    if not evidence:
        reasons.add('no_evidence')
    if len(identities) > 1:
        reasons.add('conflicting_candidates')

    changes = {}
    if not reasons and not metadata['artist'].strip():
        if snapshot['provenance']['artist'] != 'unknown':
            reasons.add('field_provenance_blocks_suggestion')
        else:
            changes['artist'] = {'before': metadata['artist'], 'after': candidates[0]['artist'],
                                 'evidence_indices': list(range(len(evidence)))}
    blockers = ['review_only_policy', 'recording_identity_unverified']
    if (any(snapshot['provenance'][field] == 'unknown' for field in ('artist', 'title'))
            or snapshot['metadata_revision'] is None or snapshot['protection_revision'] is None):
        blockers.append('provenance_or_revisions_unknown')
    return {
        'schema_version': 1,
        'policy_version': 'wanted-source-title-review-v1',
        'parser': 'wanted.services.parsers.parse_video_title',
        'receipt': deepcopy(receipt), 'evidence': deepcopy(evidence),
        'candidates': candidates, 'changes': changes,
        'status': 'uncertain' if reasons else ('proposed' if changes else 'unchanged'),
        'reasons': sorted(reasons),
        'applicable': False, 'application_blockers': blockers,
    }
