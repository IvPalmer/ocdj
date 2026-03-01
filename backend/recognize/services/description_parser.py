import logging
import re

logger = logging.getLogger(__name__)


def parse_tracklist_from_description(info_dict):
    """Parse tracklist from video description and chapters. Returns [{artist, title, timestamp}]."""
    tracks = []

    # 1. Parse chapters if available
    chapters = info_dict.get('chapters') or []
    for ch in chapters:
        title = ch.get('title', '')
        start = ch.get('start_time', 0)
        parsed = _parse_track_line(title)
        if parsed:
            parsed['timestamp'] = int(start)
            tracks.append(parsed)

    if tracks:
        logger.info(f'Found {len(tracks)} tracks from chapters')
        return tracks

    # 2. Parse description for timestamp patterns
    description = info_dict.get('description', '')
    if not description:
        return tracks

    for line in description.split('\n'):
        line = line.strip()
        if not line:
            continue

        # Try to extract timestamp + track info
        parsed = _parse_timestamped_line(line)
        if parsed:
            tracks.append(parsed)

    logger.info(f'Found {len(tracks)} tracks from description')
    return tracks


def _parse_timestamped_line(line):
    """Parse a line with a timestamp and track info.

    Supports:
      00:00 Artist - Title
      00:00:00 Artist - Title
      1. Artist - Title [00:00]
      Artist - Title (00:00:00)
      [00:00] Artist - Title
    """
    # Pattern: timestamp at start
    m = re.match(r'^\[?(\d{1,2}:)?(\d{1,2}):(\d{2})\]?\s*[-–.]?\s*(.+)', line)
    if m:
        hours = int(m.group(1).rstrip(':')) if m.group(1) else 0
        minutes = int(m.group(2))
        seconds = int(m.group(3))
        timestamp = hours * 3600 + minutes * 60 + seconds
        track_text = m.group(4).strip()
        # Remove leading track numbers like "1." or "01."
        track_text = re.sub(r'^\d+\.\s*', '', track_text)
        parsed = _parse_track_line(track_text)
        if parsed:
            parsed['timestamp'] = timestamp
            return parsed

    # Pattern: timestamp at end in brackets/parens
    m = re.match(r'^(?:\d+\.\s*)?(.+?)\s*[\(\[]\s*(\d{1,2}:)?(\d{1,2}):(\d{2})\s*[\)\]]', line)
    if m:
        track_text = m.group(1).strip()
        hours = int(m.group(2).rstrip(':')) if m.group(2) else 0
        minutes = int(m.group(3))
        seconds = int(m.group(4))
        timestamp = hours * 3600 + minutes * 60 + seconds
        parsed = _parse_track_line(track_text)
        if parsed:
            parsed['timestamp'] = timestamp
            return parsed

    return None


def _parse_track_line(text):
    """Parse 'Artist - Title' from a text string."""
    if not text:
        return None

    # Remove common suffixes
    text = re.sub(r'\s*\(?(Official\s*)?(Music\s*)?(Video|Audio|Lyric[s]?|Visuali[sz]er)\)?', '', text, flags=re.IGNORECASE)

    # Split on ' - ' or ' – '
    parts = re.split(r'\s+[-–]\s+', text, maxsplit=1)
    if len(parts) == 2:
        artist, title = parts[0].strip(), parts[1].strip()
        if artist and title:
            return {'artist': artist, 'title': title}

    # If no separator, return as title only
    text = text.strip()
    if text:
        return {'artist': '', 'title': text}

    return None
