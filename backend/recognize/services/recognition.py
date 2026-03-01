import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

# Recognition configuration
RATE_LIMIT_SECONDS = 1.5   # Delay between Shazam requests
REQUEST_TIMEOUT = 15       # Timeout per Shazam request (seconds)
MAX_RETRIES = 2            # Retries per segment on failure


async def _recognize_single(shazam, segment_path, timeout=REQUEST_TIMEOUT, retries=MAX_RETRIES):
    """Recognize a single audio segment with timeout and retry."""
    for attempt in range(retries):
        try:
            result = await asyncio.wait_for(
                shazam.recognize(segment_path),
                timeout=timeout,
            )
            return result
        except asyncio.TimeoutError:
            if attempt < retries - 1:
                logger.warning(f'Timeout on attempt {attempt + 1} for {segment_path}, retrying')
                await asyncio.sleep(2)
            else:
                logger.debug(f'Timed out after {retries} attempts for {segment_path}')
                return None
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f'Attempt {attempt + 1} failed for {segment_path}: {e}, retrying')
                await asyncio.sleep(2)
            else:
                logger.warning(f'Failed after {retries} attempts for {segment_path}: {e}')
                return None
    return None


async def _recognize_all(segments, progress_counter, timeout=REQUEST_TIMEOUT, retries=MAX_RETRIES):
    """Recognize all segments sequentially with rate limiting."""
    from shazamio import Shazam

    shazam = Shazam()
    results = []

    for i, (seg_path, start_sec) in enumerate(segments):
        result = await _recognize_single(shazam, seg_path, timeout=timeout, retries=retries)

        track_info = None
        confidence_score = 0.0

        if result and result.get('track'):
            track = result['track']
            matches = result.get('matches', [])
            # Normalize confidence: more fingerprint matches = higher confidence
            confidence_score = min(len(matches) / 5.0, 1.0) if matches else 0.5

            track_info = {
                'title': track.get('title', ''),
                'artist': track.get('subtitle', ''),
                'key': track.get('key', ''),
                'shazam_url': track.get('url', ''),
                'apple_music_url': _extract_apple_music_url(track),
                'album': _extract_metadata(track, 'Album'),
                'label': _extract_metadata(track, 'Label'),
                'confidence_score': confidence_score,
            }

        results.append({
            'start_sec': start_sec,
            'track': track_info,
            'engine': 'shazam',
            'confidence_score': confidence_score,
        })

        # Thread-safe counter update (no Django ORM here)
        progress_counter['done'] = i + 1

        # Rate limit (skip delay after last segment)
        if i < len(segments) - 1:
            await asyncio.sleep(RATE_LIMIT_SECONDS)

    return results


def recognize_segments(segments, on_progress=None, timeout=REQUEST_TIMEOUT, retries=MAX_RETRIES):
    """Recognize audio segments via ShazamIO.

    Args:
        segments: List of (segment_path, start_seconds) tuples
        on_progress: Callback(done, total) for progress updates, called from a
                     separate thread (safe for Django ORM calls)
        timeout: Per-request timeout in seconds
        retries: Number of retry attempts per segment

    Returns:
        List of {start_sec, track: {title, artist, key, confidence_score, ...} or None,
                 engine: str, confidence_score: float}
    """
    progress_counter = {'done': 0}
    total = len(segments)

    # Run progress reporting in a separate thread so Django ORM calls
    # don't conflict with the asyncio event loop
    stop_event = threading.Event()

    def _report_progress():
        last_reported = 0
        while not stop_event.is_set():
            current = progress_counter['done']
            if current > last_reported and on_progress:
                on_progress(current, total)
                last_reported = current
            if current >= total:
                break
            stop_event.wait(timeout=2)

    if on_progress:
        progress_thread = threading.Thread(target=_report_progress, daemon=True)
        progress_thread.start()

    try:
        results = asyncio.run(_recognize_all(segments, progress_counter, timeout=timeout, retries=retries))
    finally:
        stop_event.set()
        if on_progress:
            progress_thread.join(timeout=5)

    return results


def _extract_apple_music_url(track):
    """Extract Apple Music URL from Shazam track data."""
    for section in track.get('sections', []):
        for meta in section.get('metapages', []):
            url = meta.get('url', '')
            if 'music.apple.com' in url:
                return url
    # Also check hub actions
    for action in track.get('hub', {}).get('actions', []):
        uri = action.get('uri', '')
        if 'music.apple.com' in uri:
            return uri
    return ''


def _extract_metadata(track, field_name):
    """Extract a metadata field (Album, Label, etc.) from Shazam track sections."""
    for section in track.get('sections', []):
        for meta in section.get('metadata', []):
            if meta.get('title', '').lower() == field_name.lower():
                return meta.get('text', '')
    return ''
