import logging
import os

logger = logging.getLogger(__name__)


def segment_audio(audio_path, segment_duration=10, step=15):
    """Split audio into segments for recognition.

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
