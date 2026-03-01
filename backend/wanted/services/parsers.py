import re


def parse_video_title(raw_title):
    """Parse a video title into artist and track title.

    Strips common suffixes, leading track numbers, and splits on separators.
    Returns dict with artist, title, raw_title.
    """
    if not raw_title:
        return {'artist': '', 'title': '', 'raw_title': raw_title or ''}

    cleaned = raw_title.strip()

    # Strip common suffixes like (Official Video), [Official Audio], (HQ), etc.
    suffix_patterns = [
        r'\s*[\(\[]\s*(?:official\s+)?(?:video|audio|music\s+video|lyric\s+video|visualizer|clip)\s*[\)\]]',
        r'\s*[\(\[]\s*(?:HQ|HD|4K|1080p|720p|lyrics?)\s*[\)\]]',
        r'\s*[\(\[]\s*(?:full\s+)?(?:album|EP)\s*[\)\]]',
        r'\s*[\(\[]\s*(?:original\s+mix|extended\s+mix|remix)\s*[\)\]]',
        r'\s*[\(\[]\s*(?:out\s+now|free\s+download|premiere)\s*[\)\]]',
    ]
    for pattern in suffix_patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

    # Strip leading track numbers: "01.", "1 -", "01 ", "1. ", etc.
    cleaned = re.sub(r'^\d{1,3}\s*[\.\)\-]\s*', '', cleaned)

    # Try splitting on common separators
    for sep in [' - ', ' -- ', ' — ', ' | ', ' // ']:
        if sep in cleaned:
            parts = cleaned.split(sep, 1)
            artist = parts[0].strip()
            title = parts[1].strip()
            if artist and title:
                return {'artist': artist, 'title': title, 'raw_title': raw_title}

    # Fallback: entire string as title
    return {'artist': '', 'title': cleaned.strip(), 'raw_title': raw_title}
