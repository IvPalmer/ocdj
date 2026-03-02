import logging

logger = logging.getLogger(__name__)

# Clustering configuration
NONE_TOLERANCE = 2       # Max consecutive Nones before splitting a group
DEDUP_WINDOW_SEC = 60    # Only dedup same track if within this many seconds
MIN_SEGMENT_HITS = 2     # Minimum segment hits to include in tracklist (filters false positives)


def cluster_results(raw_results, description_tracks=None):
    """Cluster consecutive recognition results into a tracklist.

    Groups consecutive segments that recognized the same track,
    tolerating up to NONE_TOLERANCE consecutive None results within a group.
    Uses position-aware deduplication (allows repeated tracks if far apart).
    Assigns 5-tier confidence levels.

    Args:
        raw_results: List of {start_sec, track: {...} or None} sorted by start_sec
        description_tracks: Optional list from description parser for cross-validation

    Returns:
        List of track dicts sorted by timestamp_start
    """
    if not raw_results:
        return []

    # Group consecutive segments with same track, tolerating Nones
    groups = _group_segments(raw_results)

    # Build tracklist with position-aware dedup and 5-tier confidence
    desc_set = _build_description_set(description_tracks)
    tracklist = []
    last_seen = {}  # key -> timestamp_end of last occurrence

    for group in groups:
        key = group['key']
        segments = group['segments']
        seg_count = len(segments)

        timestamp_start = segments[0]['start_sec']
        timestamp_end = segments[-1]['start_sec'] + 10  # approximate segment coverage

        track = group['track']

        # Filter single-segment results (almost always false positives in mix recognition)
        if seg_count < MIN_SEGMENT_HITS:
            logger.debug(f'Filtering single-segment track: {track.get("artist")} - {track.get("title")} '
                         f'at {timestamp_start}s ({seg_count} hit)')
            continue

        # Position-aware dedup: only skip if same track appeared within DEDUP_WINDOW_SEC
        if key in last_seen:
            prev_end = last_seen[key]
            if timestamp_start - prev_end < DEDUP_WINDOW_SEC:
                continue

        # Compute average confidence score from Shazam matches
        scores = [s.get('confidence_score', 0) for s in segments if s.get('confidence_score')]
        avg_score = sum(scores) / len(scores) if scores else 0

        # Cross-validate with description tracks
        in_description = _check_description_match(track, desc_set)

        # 5-tier confidence system
        confidence = _compute_confidence(seg_count, avg_score, in_description)

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
            'confidence_score': round(avg_score, 2),
            'segment_count': seg_count,
            'engines': list(set(
                s.get('engine', 'shazam') for s in segments if s.get('engine')
            )),
            'in_description': in_description,
        })

        last_seen[key] = timestamp_end

    tracklist.sort(key=lambda t: t['timestamp_start'])
    logger.info(f'Clustered {len(raw_results)} results into {len(tracklist)} tracks')
    return tracklist


def _group_segments(raw_results):
    """Group consecutive segments, tolerating up to NONE_TOLERANCE consecutive Nones."""
    groups = []
    current_group = None
    none_count = 0

    for result in raw_results:
        track = result.get('track')

        if not track:
            none_count += 1
            if none_count > NONE_TOLERANCE and current_group:
                groups.append(current_group)
                current_group = None
            continue

        track_key = track.get('key') or f"{track['artist']}:{track['title']}"

        if current_group and current_group['key'] == track_key:
            # Same track — extend the group, reset None counter
            current_group['segments'].append(result)
            none_count = 0
        else:
            # Different track — save previous group and start new one
            if current_group:
                groups.append(current_group)
            current_group = {
                'key': track_key,
                'track': track,
                'segments': [result],
            }
            none_count = 0

    if current_group:
        groups.append(current_group)

    return groups


def _compute_confidence(seg_count, avg_score, in_description):
    """Compute confidence level.

    Tiers (single-segment results are pre-filtered by MIN_SEGMENT_HITS):
        verified  — 4+ segment hits, or cross-validated with description
        high      — 3+ segments, or 2+ with strong Shazam confidence (>0.7)
        medium    — 2 segment hits
        low       — 1 segment hit (only seen if MIN_SEGMENT_HITS=1)
    """
    if seg_count >= 4 or (seg_count >= 2 and in_description):
        return 'verified'
    if seg_count >= 3 or (seg_count >= 2 and avg_score > 0.7):
        return 'high'
    if seg_count >= 2:
        return 'medium'
    return 'low'


def find_single_segment_candidates(raw_results):
    """Find single-segment results that could be verified with additional scanning.

    Returns:
        List of (start_sec, track_key) tuples for single-hit tracks
        that could be real tracks worth verifying.
    """
    groups = _group_segments(raw_results)
    candidates = []

    for group in groups:
        if len(group['segments']) == 1:
            seg = group['segments'][0]
            # Only verify segments with decent confidence (skip very weak matches)
            if seg.get('confidence_score', 0) > 0.3:
                candidates.append((seg['start_sec'], group['key']))

    logger.info(f'Found {len(candidates)} single-segment candidates for verification')
    return candidates


def find_gaps(raw_results, duration_seconds, min_gap=30, step=10):
    """Find unidentified gaps in recognition results.

    Args:
        raw_results: List of {start_sec, track} sorted by start_sec
        duration_seconds: Total audio duration in seconds
        min_gap: Minimum gap size in seconds to report
        step: Segment step interval (must match pass 1 segmentation)

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
