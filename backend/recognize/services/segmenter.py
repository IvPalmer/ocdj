import logging
import os

logger = logging.getLogger(__name__)


def segment_audio(audio_path, segment_duration=5, step=10):
    """Split audio into segments for recognition. Reuses existing segments if present.

    Args:
        audio_path: Path to the audio file
        segment_duration: Length of each segment in seconds
        step: Spacing between segment start times in seconds

    Returns:
        List of (segment_file_path, start_time_seconds)
    """
    from pydub import AudioSegment

    audio = AudioSegment.from_file(audio_path)
    total_ms = len(audio)
    total_sec = total_ms / 1000

    output_dir = os.path.join(os.path.dirname(audio_path), 'segments')
    os.makedirs(output_dir, exist_ok=True)

    # Check for existing segments matching this step pattern
    expected_starts = []
    start_ms = 0
    while start_ms < total_ms:
        expected_starts.append(start_ms // 1000)
        start_ms += step * 1000

    existing = {}
    for f in os.listdir(output_dir):
        if f.startswith('seg_') and f.endswith('.mp3'):
            sec = int(f.replace('seg_', '').replace('.mp3', ''))
            existing[sec] = os.path.join(output_dir, f)

    # If all expected segments exist, reuse them
    if all(s in existing for s in expected_starts):
        segments = [(existing[s], s) for s in expected_starts]
        logger.info(f'Reusing {len(segments)} existing segments ({segment_duration}s every {step}s)')
        return segments

    # Otherwise, create all segments fresh
    segments = []
    start_ms = 0

    while start_ms < total_ms:
        end_ms = min(start_ms + segment_duration * 1000, total_ms)
        segment = audio[start_ms:end_ms]

        start_sec = start_ms // 1000
        seg_path = os.path.join(output_dir, f'seg_{start_sec:06d}.mp3')
        segment.export(seg_path, format='mp3')

        segments.append((seg_path, start_sec))
        start_ms += step * 1000

    logger.info(f'Created {len(segments)} segments from {total_sec:.0f}s audio '
                f'({segment_duration}s every {step}s)')
    return segments


def segment_gaps(audio_path, gaps, segment_duration=12, step=8):
    """Create segments for unidentified gaps in the tracklist.

    Args:
        audio_path: Path to the audio file
        gaps: List of (start_sec, end_sec) tuples for unidentified regions
        segment_duration: Length of each segment in seconds
        step: Spacing between segments within each gap

    Returns:
        List of (segment_file_path, start_time_seconds)
    """
    from pydub import AudioSegment

    audio = AudioSegment.from_file(audio_path)
    total_ms = len(audio)

    output_dir = os.path.join(os.path.dirname(audio_path), 'gap_segments')
    os.makedirs(output_dir, exist_ok=True)

    segments = []

    for gap_start, gap_end in gaps:
        start_ms = gap_start * 1000
        gap_end_ms = min(gap_end * 1000, total_ms)

        while start_ms < gap_end_ms:
            end_ms = min(start_ms + segment_duration * 1000, gap_end_ms)
            # Skip very short segments
            if end_ms - start_ms < 3000:
                break

            segment = audio[start_ms:end_ms]
            start_sec = start_ms // 1000
            seg_path = os.path.join(output_dir, f'gap_{start_sec:06d}.mp3')
            segment.export(seg_path, format='mp3')

            segments.append((seg_path, start_sec))
            start_ms += step * 1000

    logger.info(f'Created {len(segments)} gap segments from {len(gaps)} gaps')
    return segments


def segment_verification(audio_path, candidates, segment_duration=5, offsets=(-5, 5)):
    """Create verification segments for single-hit tracks.

    For each candidate timestamp, create segments at offset positions
    to check if Shazam can identify the same track from a different slice.

    Args:
        audio_path: Path to the audio file
        candidates: List of (start_sec, track_key) tuples
        segment_duration: Length of each verification segment
        offsets: Offsets from the original start_sec to try

    Returns:
        List of (segment_file_path, start_time_seconds) tuples
    """
    from pydub import AudioSegment

    audio = AudioSegment.from_file(audio_path)
    total_ms = len(audio)

    output_dir = os.path.join(os.path.dirname(audio_path), 'verify_segments')
    os.makedirs(output_dir, exist_ok=True)

    segments = []
    seen_starts = set()

    for start_sec, _track_key in candidates:
        for offset in offsets:
            verify_start = max(0, start_sec + offset)
            # Skip duplicates and out-of-range
            if verify_start in seen_starts:
                continue
            start_ms = verify_start * 1000
            if start_ms >= total_ms:
                continue

            end_ms = min(start_ms + segment_duration * 1000, total_ms)
            if end_ms - start_ms < 3000:
                continue

            segment = audio[start_ms:end_ms]
            seg_path = os.path.join(output_dir, f'verify_{verify_start:06d}.mp3')
            segment.export(seg_path, format='mp3')

            segments.append((seg_path, verify_start))
            seen_starts.add(verify_start)

    logger.info(f'Created {len(segments)} verification segments from {len(candidates)} candidates')
    return segments
