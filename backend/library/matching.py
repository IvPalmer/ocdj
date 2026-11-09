"""Conservative index-only matching. No filesystem access or source inference."""
import re


MAC_KEY = re.compile(r'mac-snapshot:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):([0-9a-f]{64})')


def match_key(value):
    if not isinstance(value, str) or len(value) > 500 or not value.strip():
        return ''
    # Preserve punctuation, accents, internal spacing and all version qualifiers.
    return value.strip(' ').lower()


def match_result(rows, missing=False):
    def measurement(value):
        return value if type(value) is int and value > 0 else None

    result = {
        'status': ('matched' if len(rows) == 1 else 'multiple_matches') if rows
                  else 'indexed_missing' if missing else 'no_indexed_match',
        'coverage': 'indexed_only',
        'matches': [dict(id=row['id'], format=row.get('format', ''),
                         bitrate=measurement(row.get('bitrate')),
                         sample_rate=measurement(row.get('sample_rate'))) for row in rows[:3]],
    }
    if len(rows) == 1 and rows[0].get('source') == 'mac_snapshot':
        key = MAC_KEY.fullmatch(rows[0].get('file_path') or '')
        if key:
            result['matches'][0]['reveal_url'] = f'ocdj-reveal://track/{key[1]}/{key[2]}'
    return result
