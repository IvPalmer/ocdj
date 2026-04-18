import os
import re
import logging

logger = logging.getLogger(__name__)

# User convention (2026-04-17): `Artist - Title` only. Keep parenthetical
# remix / version annotations that already live in the title. Drop catalog
# numbers, labels, URL stamps, and leading track-position markers — those
# belong in ID3 tags, not the filename.
DEFAULT_TEMPLATE = '{artist} - {title}'


# ── Title / artist cleaners ───────────────────────────────────────────

# [CATALOG] at end: all-caps/digits optionally with separator.
_CATALOG_BRACKET_RE = re.compile(r'\s*\[\s*[A-Z0-9][A-Z0-9\- .]*\s*\]\s*$', re.IGNORECASE)
# Website stamp tail: " - www.anything.tld" plus anything after (including
# underscored tracking suffixes like `www.x.com_639119973180043201`).
_URL_STAMP_RE = re.compile(
    r'\s*[-–—]?\s*(?:https?://)?(?:www\.)?[a-z0-9\-]+\.(?:com|net|org|co|io)(?:[/\w\-_\.]*)?.*$',
    re.IGNORECASE,
)
# Leading track markers.
#  - Vinyl-side: "A1", "A1.", "A1 ", "A2_" — always strip.
#  - Numeric "01.", "01_", "01 - " — always strip.
#  - Plain "NN " with no separator is ambiguous ("02 Raw Silk" = track 02;
#    "97 Sounds Better" = part of the real title). Bias toward stripping
#    when NN <= 30 (the practical track-position range on physical media).
_TRACK_PREFIX_RE = re.compile(
    r'^\s*(?:'
    r'[A-F]\d{1,2}\s*[.\-_]?\s*(?=\S)'   # A1., A1 , A2_, B3-
    r'|\d{1,2}\s*[._]\s*(?=\S)'           # 01., 01_
    r'|\d{1,2}\s+-\s+'                    # 01 -
    r')',
    re.IGNORECASE,
)
# Plain-digit fallback: "NN "/“N ” at start, only when NN is a plausible
# track number (≤30). Runs AFTER _TRACK_PREFIX_RE in case the explicit form
# didn't match.
_LEADING_NUM_RE = re.compile(r'^\s*0*(\d{1,2})\s+(?=\S)')
# Leading underscores from "03_Urban_Myths_..." style filenames.
_UNDERSCORE_TITLE_RE = re.compile(r'_+')

# Accidental extension leftover in the title string, e.g. "Track.flac".
_TRAILING_EXT_RE = re.compile(r'\.(?:mp3|flac|aiff|aif|wav|m4a|ogg)$', re.IGNORECASE)

# Non-remix version labels — user wants parentheticals ONLY when they denote
# a remix/distinct version, not the baseline "Original Mix" marker.
_NON_REMIX_LABEL_RE = re.compile(
    r'\s*\((?:original(?:\s+mix)?|main\s+mix|album\s+version)\)\s*',
    re.IGNORECASE,
)


def _clean_segment(s: str) -> str:
    """Strip filename noise that should only live in tags."""
    if not s:
        return ''
    s = s.strip()
    # Convert underscore-only titles to spaces early so every other regex
    # treats them like the normal space-separated case.
    if '_' in s and ' ' not in s:
        s = _UNDERSCORE_TITLE_RE.sub(' ', s)
    s = _TRAILING_EXT_RE.sub('', s)
    s = _URL_STAMP_RE.sub('', s)
    s = _CATALOG_BRACKET_RE.sub('', s)
    s = _TRACK_PREFIX_RE.sub('', s)
    # Plain leading number: strip only when the value is within the normal
    # track-position range. Preserves titles like "97 Sounds Better".
    m = _LEADING_NUM_RE.match(s)
    if m and int(m.group(1)) <= 30:
        s = s[m.end():]
    s = _NON_REMIX_LABEL_RE.sub(' ', s)
    return re.sub(r'\s+', ' ', s).strip(' -–—.')


def _strip_artist_prefix(title: str, artist: str) -> str:
    """If the title starts with the artist name (case-insensitive), drop it.

    Fixes filename-derived titles like "Urban Myths I Just Can't Help" when the
    artist is "Urban Myths". Also strips a trailing separator after the artist.
    """
    if not title or not artist:
        return title
    ts = title.strip()
    aw = artist.strip()
    if not aw:
        return title
    if ts.lower().startswith(aw.lower()):
        rest = ts[len(aw):].lstrip(' -–—_.')
        # Guard: don't strip if the rest would be empty (title literally IS
        # the artist name) or identical to the artist (avoids clearing legit
        # self-titled tracks).
        if rest and rest.lower() != aw.lower():
            return rest
    return title


def clean_artist(artist: str) -> str:
    return _clean_segment(artist or '')


def clean_title(title: str) -> str:
    return _clean_segment(title or '')


def sanitize_filename(name: str) -> str:
    """Final pass: strip FS-invalid chars and whitespace collapse."""
    name = re.sub(r'\[\s*\]', '', name)
    name = re.sub(r'\(\s*\)', '', name)
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = name.rstrip('. ')
    return name


def rename_file(pipeline_item):
    """Rename a file based on the configured template and metadata."""
    from core.services.config import get_config

    template = get_config('ORGANIZE_RENAME_TEMPLATE') or DEFAULT_TEMPLATE

    artist = clean_artist(pipeline_item.artist) or 'Unknown Artist'
    title = clean_title(pipeline_item.title) or 'Unknown Title'
    title = _strip_artist_prefix(title, artist)

    vars_ = {
        'artist': artist,
        'title': title,
        'album': pipeline_item.album or '',
        'label': pipeline_item.label or '',
        'catalog': pipeline_item.catalog_number or '',
        'genre': pipeline_item.genre or '',
        'year': pipeline_item.year or '',
        'track': pipeline_item.track_number or '',
    }

    try:
        new_name = template.format(**vars_)
    except KeyError as e:
        logger.warning(f'Invalid template variable {e}, using default')
        new_name = DEFAULT_TEMPLATE.format(**vars_)

    new_name = sanitize_filename(new_name)
    if not new_name:
        new_name = sanitize_filename(pipeline_item.original_filename)

    _, ext = os.path.splitext(pipeline_item.current_path)
    new_filename = f'{new_name}{ext}'

    current_dir = os.path.dirname(pipeline_item.current_path)
    new_path = os.path.join(current_dir, new_filename)

    if os.path.exists(new_path) and new_path != pipeline_item.current_path:
        counter = 1
        while os.path.exists(new_path):
            new_path = os.path.join(current_dir, f'{new_name}_{counter}{ext}')
            counter += 1

    if pipeline_item.current_path != new_path:
        os.rename(pipeline_item.current_path, new_path)

    pipeline_item.current_path = new_path
    pipeline_item.final_filename = os.path.basename(new_path)
    pipeline_item.save(update_fields=['current_path', 'final_filename'])
