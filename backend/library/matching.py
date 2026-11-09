"""Conservative index-only matching. No filesystem access or source inference."""


def match_key(value):
    if not isinstance(value, str) or len(value) > 500 or not value.strip():
        return ''
    # Preserve punctuation, accents, internal spacing and all version qualifiers.
    return value.strip(' ').lower()


def match_result(rows, missing=False):
    def measurement(value):
        return value if type(value) is int and value > 0 else None

    return {
        'status': ('matched' if len(rows) == 1 else 'multiple_matches') if rows
                  else 'indexed_missing' if missing else 'no_indexed_match',
        'coverage': 'indexed_only',
        'matches': [dict(id=row['id'], format=row.get('format', ''),
                         bitrate=measurement(row.get('bitrate')),
                         sample_rate=measurement(row.get('sample_rate'))) for row in rows[:3]],
    }
