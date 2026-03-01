import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

# Rate limit between Shazam API requests (seconds)
RATE_LIMIT_SECONDS = 1.5


async def _recognize_single(shazam, segment_path):
    """Recognize a single audio segment."""
    try:
        result = await shazam.recognize(segment_path)
        return result
    except Exception as e:
        logger.warning(f'Recognition failed for {segment_path}: {e}')
        return None


async def _recognize_all(segments, progress_counter):
    """Recognize all segments with rate limiting."""
    from shazamio import Shazam

    shazam = Shazam()
    results = []

    for i, (seg_path, start_sec) in enumerate(segments):
        result = await _recognize_single(shazam, seg_path)

        track_info = None
        if result and result.get('track'):
            track = result['track']
            track_info = {
                'title': track.get('title', ''),
                'artist': track.get('subtitle', ''),
                'key': track.get('key', ''),
                'shazam_url': track.get('url', ''),
                'apple_music_url': _extract_apple_music_url(track),
                'album': _extract_metadata(track, 'Album'),
                'label': _extract_metadata(track, 'Label'),
            }

        results.append({
            'start_sec': start_sec,
            'track': track_info,
        })

        # Thread-safe counter update (no Django ORM here)
        progress_counter['done'] = i + 1

        # Rate limit (skip delay after last segment)
        if i < len(segments) - 1:
            await asyncio.sleep(RATE_LIMIT_SECONDS)

    return results


def recognize_segments(segments, on_progress=None):
    """Recognize audio segments via ShazamIO.

    Args:
        segments: List of (segment_path, start_seconds) tuples
        on_progress: Callback(done, total) for progress updates, called from a
                     separate thread (safe for Django ORM calls)

    Returns:
        List of {start_sec, track: {title, artist, key, ...} or None}
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
            stop_event.wait(timeout=5)

    if on_progress:
        progress_thread = threading.Thread(target=_report_progress, daemon=True)
        progress_thread.start()

    try:
        results = asyncio.run(_recognize_all(segments, progress_counter))
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
