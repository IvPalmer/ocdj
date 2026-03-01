import logging

logger = logging.getLogger(__name__)


def cluster_results(raw_results, description_tracks=None):
    """Cluster consecutive recognition results into a tracklist.

    Groups consecutive segments that recognized the same track,
    then deduplicates and assigns confidence levels.

    Args:
        raw_results: List of {start_sec, track: {...} or None} sorted by start_sec
        description_tracks: Optional list from description parser for cross-validation

    Returns:
        List of track dicts sorted by timestamp_start
    """
    if not raw_results:
        return []

    # Group consecutive segments with same track
    groups = []
    current_group = None

    for result in raw_results:
        track = result.get('track')
        if not track:
            # No recognition — end current group
            if current_group:
                groups.append(current_group)
                current_group = None
            continue

        track_key = track.get('key') or f"{track['artist']}:{track['title']}"

        if current_group and current_group['key'] == track_key:
            # Same track — extend the group
            current_group['segments'].append(result)
        else:
            # New track — save previous group and start new one
            if current_group:
                groups.append(current_group)
            current_group = {
                'key': track_key,
                'track': track,
                'segments': [result],
            }

    if current_group:
        groups.append(current_group)

    # Build tracklist from groups
    desc_set = _build_description_set(description_tracks)
    tracklist = []
    seen_keys = set()

    for group in groups:
        key = group['key']
        # Skip duplicates (same track appearing multiple times)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        track = group['track']
        segments = group['segments']
        seg_count = len(segments)

        timestamp_start = segments[0]['start_sec']
        # Extend timestamp_end beyond the last segment start
        timestamp_end = segments[-1]['start_sec'] + 10  # approximate segment coverage

        # Confidence: in DJ mixes, single-segment matches are normal
        if seg_count >= 3:
            confidence = 'high'
        elif seg_count >= 2:
            confidence = 'high'
        else:
            confidence = 'medium'

        # Cross-validate with description tracks
        in_description = _check_description_match(track, desc_set)
        if in_description:
            confidence = 'high'

        tracklist.append({
            'artist': track.get('artist', ''),
            'title': track.get('title', ''),
            'album': track.get('album', ''),
            'label': track.get('label', ''),
            'timestamp_start': timestamp_start,
            'timestamp_end': timestamp_end,
            'shazam_url': track.get('shazam_url', ''),
            'apple_music_url': track.get('apple_music_url', ''),
            'confidence': confidence,
            'in_description': in_description,
        })

    tracklist.sort(key=lambda t: t['timestamp_start'])
    logger.info(f'Clustered {len(raw_results)} results into {len(tracklist)} tracks')
    return tracklist


def find_gaps(raw_results, duration_seconds, min_gap=30):
    """Find unidentified gaps in recognition results.

    Args:
        raw_results: List of {start_sec, track} sorted by start_sec
        duration_seconds: Total audio duration in seconds
        min_gap: Minimum gap size in seconds to report

    Returns:
        List of (start_sec, end_sec) tuples for gaps > min_gap seconds
    """
    if not raw_results or not duration_seconds:
        return []

    # Find regions where no track was identified
    identified_times = set()
    for result in raw_results:
        if result.get('track'):
            identified_times.add(result['start_sec'])

    gaps = []
    gap_start = None

    # Scan through the audio timeline
    step = 10  # Assume default step from pass 1
    for sec in range(0, duration_seconds, step):
        if sec in identified_times:
            if gap_start is not None and (sec - gap_start) >= min_gap:
                gaps.append((gap_start, sec))
            gap_start = None
        else:
            if gap_start is None:
                gap_start = sec

    # Handle trailing gap
    if gap_start is not None and (duration_seconds - gap_start) >= min_gap:
        gaps.append((gap_start, duration_seconds))

    logger.info(f'Found {len(gaps)} gaps > {min_gap}s')
    return gaps


def _build_description_set(description_tracks):
    """Build a set of normalized (artist, title) pairs from description tracks."""
    if not description_tracks:
        return set()

    result = set()
    for t in description_tracks:
        artist = (t.get('artist') or '').lower().strip()
        title = (t.get('title') or '').lower().strip()
        if artist or title:
            result.add((artist, title))
    return result


def _check_description_match(track, desc_set):
    """Check if a recognized track matches any description track."""
    if not desc_set:
        return False

    artist = (track.get('artist') or '').lower().strip()
    title = (track.get('title') or '').lower().strip()

    # Exact match
    if (artist, title) in desc_set:
        return True

    # Partial match — check if title appears in any description entry
    for desc_artist, desc_title in desc_set:
        if title and desc_title and (title in desc_title or desc_title in title):
            return True
        if artist and desc_artist and (artist in desc_artist or desc_artist in artist):
            if title and desc_title and (title in desc_title or desc_title in title):
                return True

    return False
