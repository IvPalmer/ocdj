import unicodedata

from rapidfuzz.fuzz import token_sort_ratio

from wanted.models import WantedItem


def _normalize(text):
    """Lowercase, strip accents, collapse whitespace."""
    if not text:
        return ''
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(text.lower().split())


def check_duplicates(tracks, threshold=85):
    """Check each track dict against existing WantedItems for duplicates.

    Mutates each track dict in-place, adding:
      - is_duplicate (bool)
      - duplicate_of_id (int or None)
      - fuzzy_score (float or None)
    """
    existing = list(
        WantedItem.objects.values_list('id', 'artist', 'title')
    )

    # Build normalized set for fast exact matching
    exact_set = {}
    for item_id, artist, title in existing:
        key = (_normalize(artist), _normalize(title))
        exact_set[key] = item_id

    for track in tracks:
        norm_artist = _normalize(track.get('artist', ''))
        norm_title = _normalize(track.get('title', ''))
        track_key = (norm_artist, norm_title)

        # Exact match
        if track_key in exact_set and norm_title:
            track['is_duplicate'] = True
            track['duplicate_of_id'] = exact_set[track_key]
            track['fuzzy_score'] = 100.0
            continue

        # Fuzzy match
        best_score = 0.0
        best_id = None
        track_str = f"{norm_artist} {norm_title}".strip()

        if not track_str:
            track['is_duplicate'] = False
            track['duplicate_of_id'] = None
            track['fuzzy_score'] = None
            continue

        for item_id, artist, title in existing:
            existing_str = f"{_normalize(artist)} {_normalize(title)}".strip()
            if not existing_str:
                continue
            score = token_sort_ratio(track_str, existing_str)
            if score > best_score:
                best_score = score
                best_id = item_id

        if best_score >= threshold:
            track['is_duplicate'] = True
            track['duplicate_of_id'] = best_id
            track['fuzzy_score'] = round(best_score, 1)
        else:
            track['is_duplicate'] = False
            track['duplicate_of_id'] = None
            track['fuzzy_score'] = round(best_score, 1) if best_score > 0 else None

    return tracks
