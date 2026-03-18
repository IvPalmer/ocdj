import logging
import re

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

logger = logging.getLogger(__name__)

# Clustering configuration
PROXIMITY_WINDOW_SEC = 120  # Max gap between segments of the same track to merge them
CONFLICT_WINDOW_SEC = 30    # If multiple different tracks hit within this window, keep only the best

# Multi-segment ACRCloud low-score acceptance — tested March 2026:
# MADVILLA - Down 4 Me hit 31 times at score 25-34 across all segment durations.
# A single hit at score 25 is noise, but 3+ hits of the same track is real signal.
ACR_LOW_SCORE_MIN_SEGMENTS = 3  # Accept score 25+ if this many segments match


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
    # Also strip dash-based remix suffixes: "Seven Days - Original Mix" → "Seven Days"
    title = re.sub(r'\s*-\s*(?:original|extended|club|radio|dub|vocal|instrumental|remix)\s*(?:mix|version|edit)?.*$', '', title, flags=re.IGNORECASE).strip()
    # Normalize abbreviations: "Pt." → "Part", "Vol." → "Volume"
    title = re.sub(r'\bpt\.?\s', 'part ', title)
    title = re.sub(r'\bvol\.?\s', 'volume ', title)
    # Strip trailing punctuation from title (e.g., "Where?" → "Where")
    title = re.sub(r'[?!.,;:]+$', '', title).strip()
    # Remove commas within title for matching ("Detroit, Pt. II" → "Detroit Part II")
    title = title.replace(',', '')
    # Collapse whitespace
    artist = re.sub(r'\s+', ' ', artist).strip()
    title = re.sub(r'\s+', ' ', title).strip()
    return f'{artist}:{title}'


def _extract_title_core(title):
    """Extract the core title words, stripping all parenthetical/remix info.

    Used for fuzzy matching covers/remixes/samples — e.g., all these share 'o superman':
      - 'O Superman (For Massenet) (Remastered)'
      - 'O Superman (Disco Spacer Mix)'
      - 'O Superman (M.A.N.D.Y. vs. Booka Shade vs. Laurie Anderson)'
    """
    t = (title or '').lower().strip()
    t = re.sub(r'\s*[\(\[].*', '', t).strip()  # Strip everything from first ( or [
    t = re.sub(r'\s*-\s*(original|extended|club|radio|dub|vocal|instrumental|remix).*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'[?!.,;:]+$', '', t)  # Strip trailing punctuation
    t = t.replace(',', '')  # Remove commas
    t = re.sub(r'\bpt\.?\s', 'part ', t)  # Normalize abbreviations
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _group_by_title_similarity(track_hits, track_info):
    """Merge track_hits entries that share the same core title (covers/remixes/samples).

    Tested March 2026: Laurie Anderson - O Superman was detected by Shazam as:
      - Marcello Giordani - O Superman (Disco Spacer Mix)
      - Mandy & Booka Shade - O Superman (M.A.N.D.Y. vs. Booka Shade vs. Laurie Anderson)
    And by ACRCloud as:
      - Age Of Luv - O Superman
    All share title core "o superman" — grouping them produces a strong multi-segment hit.
    """
    # Build core title -> list of keys mapping
    core_groups = {}  # core_title -> [key1, key2, ...]
    key_cores = {}    # key -> core_title

    for key in track_hits:
        info = track_info[key]
        core = _extract_title_core(info.get('title', ''))
        if len(core) < 3:
            continue
        key_cores[key] = core
        if core not in core_groups:
            core_groups[core] = []
        core_groups[core].append(key)

    # Only merge groups with 2+ different keys AND the core is at least 2 words
    # (to avoid merging generic single-word titles like "love" or "deep")
    # Exception: single-word titles merge when one artist is a substring of the other
    # (e.g., "Michel Banabila - Where" + "Banabila - Where?" → same track)
    merged_keys = {}  # old_key -> canonical_key
    for core, keys in core_groups.items():
        if len(keys) < 2:
            continue
        if len(core.split()) < 2:
            # Single-word title — only merge if artists overlap (substring match)
            artists = [(k, track_info[k].get('artist', '').lower()) for k in keys]
            artist_overlap_keys = []
            for i, (k1, a1) in enumerate(artists):
                for k2, a2 in artists[i+1:]:
                    if a1 and a2 and (a1 in a2 or a2 in a1):
                        if k1 not in artist_overlap_keys:
                            artist_overlap_keys.append(k1)
                        if k2 not in artist_overlap_keys:
                            artist_overlap_keys.append(k2)
            if len(artist_overlap_keys) < 2:
                continue
            keys = artist_overlap_keys

        # Check total combined segments — only merge if the group is meaningful
        total_segs = sum(len(track_hits[k]) for k in keys)
        if total_segs < 2:
            continue

        # Pick the canonical key: prefer the one with more segments, then by artist name length
        # (shorter artist = more likely the original, e.g., "Laurie Anderson" vs "Mandy & Booka Shade")
        canonical = max(keys, key=lambda k: (
            len(track_hits[k]),
            -len(track_info[k].get('artist', '')),
        ))
        for k in keys:
            if k != canonical:
                merged_keys[k] = canonical

    if not merged_keys:
        return track_hits, track_info

    # Merge hits into canonical keys
    new_hits = {}
    new_info = {}
    for key in track_hits:
        canonical = merged_keys.get(key, key)
        if canonical not in new_hits:
            new_hits[canonical] = []
            new_info[canonical] = track_info[canonical]
        new_hits[canonical].extend(track_hits[key])
        # If merged key has richer metadata (label, album), keep it
        merged_track = track_info[key]
        canonical_track = new_info[canonical]
        if merged_track.get('label') and not canonical_track.get('label'):
            new_info[canonical] = {**canonical_track, 'label': merged_track['label']}

    merged_count = len(merged_keys)
    if merged_count:
        logger.info(f'Title similarity grouping: merged {merged_count} keys into existing groups')

    return new_hits, new_info


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

    # Group covers/remixes/samples by title similarity before clustering
    # e.g., "Marcello Giordani - O Superman" + "Age Of Luv - O Superman" → merged group
    track_hits, track_info = _group_by_title_similarity(track_hits, track_info)

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


def dedup_tracklist(tracklist):
    """Remove duplicate entries for the same track that appear near each other.

    After TrackID merge, the same track can appear multiple times if ACRCloud
    detected it at timestamps beyond PROXIMITY_WINDOW_SEC. This pass merges
    entries with the same normalized key that are within 300s of each other.
    """
    if len(tracklist) <= 1:
        return tracklist

    DEDUP_WINDOW = 300  # 5 minutes — generous for DJ mixes

    result = []
    seen = {}  # normalized_key -> index in result

    for t in tracklist:
        key = _normalize_key(t.get('artist', ''), t.get('title', ''))
        if key in seen:
            prev_idx = seen[key]
            prev = result[prev_idx]
            gap = t['timestamp_start'] - prev['timestamp_end']
            if gap <= DEDUP_WINDOW:
                # Merge: extend time range and combine metadata
                prev['timestamp_end'] = max(prev['timestamp_end'], t['timestamp_end'])
                prev['segment_count'] = prev.get('segment_count', 0) + t.get('segment_count', 0)
                prev_engines = set(prev.get('engines', []))
                prev_engines.update(t.get('engines', []))
                prev['engines'] = list(prev_engines)
                if t.get('confidence') == 'verified' or prev.get('confidence') == 'verified':
                    prev['confidence'] = 'verified'
                # Keep richer metadata
                if t.get('label') and not prev.get('label'):
                    prev['label'] = t['label']
                if t.get('album') and not prev.get('album'):
                    prev['album'] = t['album']
                if t.get('shazam_url') and not prev.get('shazam_url'):
                    prev['shazam_url'] = t['shazam_url']
                continue

        seen[key] = len(result)
        result.append(t)

    deduped = len(tracklist) - len(result)
    if deduped:
        logger.info(f'Dedup: merged {deduped} duplicate track entries')

    return result


def _resolve_conflicts(tracklist):
    """Remove conflicting single-hit tracks that overlap in time.

    When multiple different tracks are identified in the same narrow time window,
    it's usually noise. Keep the strongest match and drop the rest.
    Multi-hit tracks (2+ segments) are always kept.

    Also filters ACRCloud noise: single-hit ACR results with score < 40 are dropped
    unless they have ACR_LOW_SCORE_MIN_SEGMENTS or more segments (multi-segment
    consistency overrides low individual scores — tested March 2026).
    """
    if len(tracklist) <= 1:
        return tracklist

    # Pre-filter: drop single-hit ACRCloud noise (score < 40 and only 1 segment)
    # Multi-segment low-score hits are kept — they represent real signal
    pre_filtered = []
    acr_noise_dropped = 0
    for t in tracklist:
        is_acr_only = t.get('engines') == ['acrcloud']
        is_low_score = t.get('confidence_score', 0) < 0.40
        is_single_hit = t.get('segment_count', 0) < ACR_LOW_SCORE_MIN_SEGMENTS

        if is_acr_only and is_low_score and is_single_hit:
            acr_noise_dropped += 1
            continue
        pre_filtered.append(t)

    if acr_noise_dropped:
        logger.info(f'Dropped {acr_noise_dropped} single-hit ACRCloud noise results (score < 40)')

    tracklist = pre_filtered

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

    Tiers:
        verified  — 4+ segment hits, or cross-validated with description
        high      — 3+ segments, or 2+ with strong confidence (>0.7),
                     or 3+ low-score ACRCloud segments (multi-segment consistency)
        medium    — 2 segment hits
        low       — 1 segment hit
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
