import logging
import re

logger = logging.getLogger(__name__)

# Clustering configuration
PROXIMITY_WINDOW_SEC = 120  # Max gap between segments of the same track to merge them
CONFLICT_WINDOW_SEC = 30    # If multiple different tracks hit within this window, keep only the best


def _normalize_key(artist, title):
    """Normalize track key for matching — strips 'The', punctuation, feat. tags, etc."""
    artist = (artist or '').lower().strip()
    title = (title or '').lower().strip()
    # Strip leading "the " from artist
    artist = re.sub(r'^the\s+', '', artist)
    # Remove quotes and punctuation from artist (e.g., Kenny "Dope" → kenny dope)
    artist = re.sub(r'["\'\u201c\u201d\u2018\u2019]', '', artist)
    # Remove feat./ft. from artist name (e.g., "Tek 9 feat. X" → "tek 9")
    artist = re.sub(r'\s*\b(?:feat\.?|featuring|ft\.)\s+.*$', '', artist)
    # Normalize & / and
    title = re.sub(r'\s*&\s*', ' and ', title)
    # Remove feat./featuring/ft. tags and everything after in parentheses
    # Word boundary \b prevents matching "ft" inside words like "soft"
    title = re.sub(r'\s*[\(\[]?\s*\b(?:feat\.?|featuring|ft\.)\s+.*?[\)\]]?\s*$', '', title)
    # Remove remix/dub/mix tags in parentheses for base matching
    base_title = re.sub(r'\s*[\(\[].*?[\)\]]', '', title).strip()
    # Use base title if non-empty, otherwise full title
    title = base_title if base_title else title
    # Collapse whitespace
    artist = re.sub(r'\s+', ' ', artist).strip()
    title = re.sub(r'\s+', ' ', title).strip()
    return f'{artist}:{title}'


def cluster_results(raw_results, description_tracks=None):
    """Cluster recognition results into a tracklist.

    Groups all segments of the same track within PROXIMITY_WINDOW_SEC of each other.
    Includes single-hit tracks but filters conflicting hits — when multiple different
    tracks are identified at the same timestamp, keeps only the one with the most
    segment hits (or highest confidence).

    Args:
        raw_results: List of {start_sec, track: {...} or None} sorted by start_sec
        description_tracks: Optional list from description parser for cross-validation

    Returns:
        List of track dicts sorted by timestamp_start
    """
    if not raw_results:
        return []

    # Collect all hits grouped by normalized track key
    track_hits = {}  # key -> list of result dicts
    track_info = {}  # key -> first track metadata

    for result in raw_results:
        track = result.get('track')
        if not track:
            continue
        key = _normalize_key(track.get('artist', ''), track.get('title', ''))
        if key not in track_hits:
            track_hits[key] = []
            track_info[key] = track
        track_hits[key].append(result)

    # Build raw tracklist — merge nearby hits for the same track
    desc_set = _build_description_set(description_tracks)
    raw_tracklist = []

    for key, hits in track_hits.items():
        hits.sort(key=lambda r: r['start_sec'])
        track = track_info[key]

        # Split into proximity groups (hits within PROXIMITY_WINDOW_SEC of each other)
        groups = []
        current = [hits[0]]
        for h in hits[1:]:
            if h['start_sec'] - current[-1]['start_sec'] <= PROXIMITY_WINDOW_SEC:
                current.append(h)
            else:
                groups.append(current)
                current = [h]
        groups.append(current)

        for segments in groups:
            seg_count = len(segments)
            timestamp_start = segments[0]['start_sec']
            timestamp_end = segments[-1]['start_sec'] + 10

            scores = [s.get('confidence_score', 0) for s in segments if s.get('confidence_score')]
            avg_score = sum(scores) / len(scores) if scores else 0

            in_description = _check_description_match(track, desc_set)
            confidence = _compute_confidence(seg_count, avg_score, in_description)

            engines = list(set(
                s.get('engine', 'shazam') for s in segments if s.get('engine')
            ))

            raw_tracklist.append({
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
                'engines': engines,
                'in_description': in_description,
            })

    raw_tracklist.sort(key=lambda t: t['timestamp_start'])

    # Resolve conflicts: when multiple single-hit tracks overlap within CONFLICT_WINDOW_SEC,
    # keep only the one with highest segment_count, then highest confidence_score
    tracklist = _resolve_conflicts(raw_tracklist)

    logger.info(f'Clustered {len(raw_results)} results into {len(tracklist)} tracks')
    return tracklist


def _resolve_conflicts(tracklist):
    """Remove conflicting single-hit tracks that overlap in time.

    When multiple different tracks are identified in the same narrow time window,
    it's usually noise. Keep the strongest match and drop the rest.
    Multi-hit tracks (2+ segments) are always kept.
    """
    if len(tracklist) <= 1:
        return tracklist

    # First pass: resolve single-hit conflicts within CONFLICT_WINDOW_SEC
    result = []
    i = 0
    while i < len(tracklist):
        t = tracklist[i]

        # Multi-hit tracks always pass through
        if t['segment_count'] > 1:
            result.append(t)
            i += 1
            continue

        # Collect all single-hit tracks in this time window
        conflict_group = [t]
        j = i + 1
        while j < len(tracklist) and tracklist[j]['timestamp_start'] - t['timestamp_start'] <= CONFLICT_WINDOW_SEC:
            if tracklist[j]['segment_count'] <= 1:
                conflict_group.append(tracklist[j])
            j += 1

        if len(conflict_group) > 1:
            # Multiple single-hit tracks in same window — pick the best one
            best = max(conflict_group, key=lambda x: (x['confidence_score'], x['segment_count']))
            result.append(best)
            i = j
        else:
            result.append(t)
            i += 1

    return result


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
    # Count hits per normalized track key
    track_hits = {}
    for result in raw_results:
        track = result.get('track')
        if not track:
            continue
        key = _normalize_key(track.get('artist', ''), track.get('title', ''))
        if key not in track_hits:
            track_hits[key] = []
        track_hits[key].append(result)

    candidates = []
    for key, hits in track_hits.items():
        if len(hits) == 1:
            seg = hits[0]
            if seg.get('confidence_score', 0) > 0.15:
                candidates.append((seg['start_sec'], key))

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
