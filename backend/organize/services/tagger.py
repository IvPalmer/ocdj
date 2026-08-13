import os
import logging
import shutil
import tempfile

import mutagen
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPUB, TDRC, TRCK, TCON, TXXX
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.aiff import AIFF

import re

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


# Parenthetical suffixes that are NOT musical (safe to strip)
_NOISE_PARENS = re.compile(
    r'\s*[\(\[]\s*(?:'
    r'official\s+(?:video|audio|music\s+video|lyric\s+video|visualizer|clip)'
    r'|HQ|HD|4K|1080p|720p|lyrics?'
    r'|full\s+(?:album|EP)'
    r'|out\s+now|free\s+download|premiere'
    r')\s*[\)\]]',
    re.IGNORECASE,
)


def _clean_year(value):
    """Extract a 4-digit year from a date string like '17-10-2009', '2009-10-17', '2009'."""
    if not value:
        return ''
    # Try to find a 4-digit year
    m = re.search(r'\b((?:19|20)\d{2})\b', value)
    return m.group(1) if m else value.strip()


def _clean_catalog_number(value):
    """Strip common suffixes from catalog numbers like 'Promo', 'Ltd', 'Deluxe'."""
    if not value:
        return ''
    # Remove trailing noise words
    cleaned = re.sub(
        r'\s+(?:Promo|promo|PROMO|Ltd|LTD|Limited|Deluxe|Repress|Reissue|Test\s*Press)\s*$',
        '', value.strip()
    )
    return cleaned.strip()


# Derived from the model so it can never drift out of sync with the column.
# Beatport compilation rips stamp the whole genre taxonomy into one tag
# (e.g. "House, Deep House, Tech House, ...", 261 chars), which overflows the
# column and fails tagging with a Postgres
# "value too long for type character varying(200)" error.
from organize.models import PipelineItem

_GENRE_MAX_LEN = PipelineItem._meta.get_field('genre').max_length


def _clean_genre(value):
    """Keep the primary genre when a tag crams a whole comma-separated list.

    Only intervenes when the value would overflow the DB column, so legitimate
    single genres containing commas (rare) or normal names pass through
    untouched. Falls back to a hard truncation if even the first segment is
    somehow over the limit.
    """
    if not value:
        return ''
    value = value.strip()
    if len(value) <= _GENRE_MAX_LEN:
        return value
    primary = value.split(',', 1)[0].strip()
    return primary[:_GENRE_MAX_LEN]


def _parse_title_from_filename(filename):
    """Extract artist and title from a Soulseek-style filename, preserving mix info."""
    name = os.path.splitext(filename)[0]
    # Strip leading track numbers: "01.", "34 - ", etc.
    name = re.sub(r'^\d{1,3}\s*[\.\)\-]\s*', '', name)
    # Strip only noise parens, keep musical ones like (NJ Mix), (Dub)
    name = _NOISE_PARENS.sub('', name)
    name = name.strip()

    for sep in [' - ', ' -- ', ' — ']:
        if sep in name:
            parts = name.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return '', name


def read_existing_tags(filepath):
    """Read existing tags from an audio file. Returns a dict."""
    tags = {}
    try:
        audio = mutagen.File(filepath, easy=True)
        if audio is None:
            return tags

        tag_map = {
            'artist': ['artist'],
            'title': ['title'],
            'album': ['album'],
            'genre': ['genre'],
            'date': ['date'],
            'tracknumber': ['tracknumber'],
        }

        for key, aliases in tag_map.items():
            for alias in aliases:
                val = audio.get(alias)
                if val:
                    tags[key] = val[0] if isinstance(val, list) else str(val)
                    break

        # Check for artwork
        raw = mutagen.File(filepath)
        if raw:
            if hasattr(raw, 'pictures') and raw.pictures:
                tags['has_artwork'] = True
            elif hasattr(raw, 'tags') and raw.tags:
                for key in raw.tags:
                    if 'APIC' in str(key):
                        tags['has_artwork'] = True
                        break
    except Exception as e:
        logger.warning(f"Error reading tags from {filepath}: {e}")

    return tags


def _enrich_from_discogs(artist, title, label='', catalog_number=''):
    """Try to find metadata from Discogs."""
    try:
        from core.views import get_config
        token = get_config('DISCOGS_PERSONAL_TOKEN')
        if not token:
            return None

        import discogs_client
        d = discogs_client.Client('OCDJ/2.0', user_token=token)

        # Build search query
        query_parts = []
        if artist:
            query_parts.append(artist)
        if title:
            query_parts.append(title)

        if catalog_number:
            results = d.search(catno=catalog_number, type='release')
        elif query_parts:
            results = d.search(' '.join(query_parts), type='release')
        else:
            return None

        # discogs_client paginated lists don't support Python slice syntax —
        # `results[:5]` blows up with "slice // int". Iterate with a counter.
        from itertools import islice
        for result in islice(results, 5):
            # Verify match quality with rapidfuzz
            result_artist = ', '.join(a.name for a in result.artists) if hasattr(result, 'artists') else ''
            result_title = result.title if hasattr(result, 'title') else ''

            if artist and result_artist:
                score = fuzz.token_sort_ratio(artist.lower(), result_artist.lower())
                if score < 60:
                    continue

            return {
                'artist': result_artist or artist,
                'title': result_title or title,
                'album': result_title,
                'label': result.labels[0].name if hasattr(result, 'labels') and result.labels else label,
                'catalog_number': result.labels[0].catno if hasattr(result, 'labels') and result.labels else catalog_number,
                'genre': result.genres[0] if hasattr(result, 'genres') and result.genres else '',
                'year': str(result.year) if hasattr(result, 'year') and result.year else '',
                'source': 'discogs',
            }
    except Exception as e:
        logger.warning(f"Discogs enrichment failed: {e}")

    return None


def _enrich_from_musicbrainz(artist, title):
    """Try to find metadata from MusicBrainz."""
    try:
        import musicbrainzngs
        musicbrainzngs.set_useragent('OCDJ', '2.0', 'https://github.com/ocdj')

        query = f'recording:"{title}" AND artist:"{artist}"' if artist else f'recording:"{title}"'
        results = musicbrainzngs.search_recordings(query=query, limit=5)

        for rec in results.get('recording-list', []):
            rec_title = rec.get('title', '')
            rec_artist = rec.get('artist-credit-phrase', '')

            if artist and rec_artist:
                score = fuzz.token_sort_ratio(artist.lower(), rec_artist.lower())
                if score < 60:
                    continue

            release = rec.get('release-list', [{}])[0] if rec.get('release-list') else {}

            return {
                'artist': rec_artist or artist,
                'title': rec_title or title,
                'album': release.get('title', ''),
                'label': '',
                'catalog_number': '',
                'genre': '',
                'year': release.get('date', '')[:4] if release.get('date') else '',
                'source': 'musicbrainz',
            }
    except Exception as e:
        logger.warning(f"MusicBrainz enrichment failed: {e}")

    return None


def enrich_metadata(artist, title, label='', catalog_number=''):
    """Try Discogs first, fallback to MusicBrainz."""
    result = _enrich_from_discogs(artist, title, label, catalog_number)
    if result:
        return result

    result = _enrich_from_musicbrainz(artist, title)
    if result:
        return result

    return None


def _clean_metadata(metadata: dict) -> dict:
    """Normalize artist/title in place before writing to tags.

    Filename and tag display should match: both strip catalog brackets,
    URL stamps, release-page annotations, track prefixes, and artist
    repetition in title. Musical version labels such as Remix, Edit, Dub,
    and Original Mix are intentionally retained. Applied here so any code
    path that writes tags produces the same clean result.
    """
    from .renamer import clean_artist, clean_title, clean_album, _strip_artist_prefix
    out = dict(metadata)
    a = clean_artist(out.get('artist') or '')
    t = clean_title(out.get('title') or '')
    if a:
        t = _strip_artist_prefix(t, a)
    if a:
        out['artist'] = a
    if t:
        out['title'] = t
    if out.get('album'):
        out['album'] = clean_album(out['album'])
    return out


# key → (ID3 frame id, frame class). Order is cosmetic; membership is what
# matters: a key present in the metadata dict with an empty value CLEARS the
# frame, a key that is absent leaves whatever the file already carries.
_ID3_FRAMES = (
    ('artist', 'TPE1', TPE1),
    ('title', 'TIT2', TIT2),
    ('album', 'TALB', TALB),
    ('genre', 'TCON', TCON),
    ('year', 'TDRC', TDRC),
    ('track_number', 'TRCK', TRCK),
    ('label', 'TPUB', TPUB),
)

# key → tag name for FLAC / easy-tag containers.
_EASY_KEYS = (
    ('artist', 'artist'),
    ('title', 'title'),
    ('album', 'album'),
    ('genre', 'genre'),
    ('year', 'date'),
    ('track_number', 'tracknumber'),
    ('label', 'label'),
    ('catalog_number', 'catalognumber'),
)


def _set_or_clear(container, tag_name, key, metadata):
    """Assign the tag when the value is non-empty, delete it when it's empty.

    Only touches tags whose key the caller actually supplied. Clearing a field
    in the UI used to be a no-op on the file: write_tags skipped falsy values,
    so the old album/genre stayed embedded and Music.app kept showing it.
    """
    if key not in metadata:
        return
    value = metadata.get(key)
    if value:
        container[tag_name] = value
    elif tag_name in container:
        del container[tag_name]


def write_tags(filepath, metadata):
    """Write tags to an audio file using mutagen. Format-aware.

    A key present with an empty value clears that tag; an absent key is left
    alone. Callers that build a partial dict (the pipeline tagger) therefore
    keep their additive behaviour, while callers that submit the whole form
    (manual edit, published-artifact refresh) can actually erase a field.
    """
    metadata = _clean_metadata(metadata)
    try:
        audio = mutagen.File(filepath)
        if audio is None:
            logger.warning(f"Cannot identify audio format: {filepath}")
            return

        ext = os.path.splitext(filepath)[1].lower()

        if ext in ('.mp3', '.aiff', '.aif', '.wav'):
            # ID3 tags. mutagen.File(easy=True) doesn't wrap WAV, so we
            # always open the container-specific class here, which supports
            # raw ID3 frames for all three wrappers (MP3 / AIFF / WAVE).
            if ext == '.mp3':
                audio = MP3(filepath)
            elif ext in ('.aiff', '.aif'):
                audio = AIFF(filepath)
            else:  # .wav
                from mutagen.wave import WAVE
                audio = WAVE(filepath)
            if audio.tags is None:
                audio.add_tags()

            tags = audio.tags
            for key, frame_id, frame_cls in _ID3_FRAMES:
                if key not in metadata:
                    continue
                value = metadata.get(key)
                if value:
                    tags[frame_id] = frame_cls(encoding=3, text=[value])
                else:
                    tags.delall(frame_id)
            if 'catalog_number' in metadata:
                tags.delall('TXXX:CATALOGNUMBER')
                if metadata['catalog_number']:
                    tags.add(TXXX(encoding=3, desc='CATALOGNUMBER',
                                  text=[metadata['catalog_number']]))

            audio.save()

        elif ext == '.flac':
            flac = FLAC(filepath)
            for key, tag_name in _EASY_KEYS:
                _set_or_clear(flac, tag_name, key, metadata)

            flac.save()

        else:
            # Use easy tags as fallback for other formats
            audio = mutagen.File(filepath, easy=True)
            if audio is not None:
                for key, tag_name in _EASY_KEYS:
                    if tag_name in ('label', 'catalognumber', 'tracknumber'):
                        # EasyMP4/EasyID3 reject unknown keys with a KeyError;
                        # only the core five are portable across containers.
                        continue
                    _set_or_clear(audio, tag_name, key, metadata)
                audio.save()
    except Exception as e:
        logger.error(f"Error writing tags to {filepath}: {e}")
        raise


def write_tags_atomic(filepath, metadata):
    """Tag a copy of the file, then replace the original with os.replace().

    In-place tagging is fine on the workbench but not on a published artifact:
    the drain daemon (or a browser download) can be reading the same bytes, and
    a mutagen failure half-way through leaves the file rewritten even though
    the caller saw an exception. Writing a sibling temp file and swapping it in
    means readers see either the old file or the new one, never a mix.
    """
    directory = os.path.dirname(filepath) or '.'
    ext = os.path.splitext(filepath)[1]
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.ocdj-tag-', suffix=ext)
    os.close(fd)
    try:
        shutil.copy2(filepath, tmp_path)
        write_tags(tmp_path, metadata)
        os.replace(tmp_path, filepath)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def tag_file(pipeline_item):
    """Orchestrate tagging: read existing -> merge WantedItem data -> enrich -> write -> update item."""
    filepath = pipeline_item.current_path

    # Read existing tags from file
    existing = read_existing_tags(filepath)

    # Build metadata dict: existing file tags -> WantedItem fields -> pipeline item fields
    metadata = {}

    # Start with existing file tags
    if existing.get('artist'):
        metadata['artist'] = existing['artist']
    if existing.get('title'):
        metadata['title'] = existing['title']
    if existing.get('album'):
        metadata['album'] = existing['album']
    if existing.get('genre'):
        metadata['genre'] = existing['genre']
    if existing.get('date'):
        metadata['year'] = existing['date']
    if existing.get('tracknumber'):
        metadata['track_number'] = existing['tracknumber']
    metadata['has_artwork'] = existing.get('has_artwork', False)

    # Parse the original filename — it often has mix/version info the WantedItem lacks
    fn_artist, fn_title = _parse_title_from_filename(pipeline_item.original_filename)

    # Override with WantedItem / pipeline item data (more authoritative for base info)
    if pipeline_item.artist:
        metadata['artist'] = pipeline_item.artist
    if pipeline_item.title:
        # If the filename title contains extra info (e.g. a mix name) that the
        # WantedItem title doesn't, prefer the richer filename version.
        wi_title = pipeline_item.title.strip()
        if fn_title and wi_title and fn_title.lower() != wi_title.lower():
            # Check if filename title starts with the wanted title (i.e. it's a superset)
            if fn_title.lower().startswith(wi_title.lower()):
                metadata['title'] = fn_title
            else:
                metadata['title'] = wi_title
        else:
            metadata['title'] = wi_title
    elif fn_title:
        metadata['title'] = fn_title
    if not metadata.get('artist') and fn_artist:
        metadata['artist'] = fn_artist
    if pipeline_item.album:
        metadata['album'] = pipeline_item.album
    if pipeline_item.label:
        metadata['label'] = pipeline_item.label
    if pipeline_item.catalog_number:
        metadata['catalog_number'] = pipeline_item.catalog_number

    source = 'file'

    # Try enrichment from external sources
    enriched = enrich_metadata(
        metadata.get('artist', ''),
        metadata.get('title', ''),
        metadata.get('label', ''),
        metadata.get('catalog_number', ''),
    )

    if enriched:
        source = enriched.pop('source', 'file')
        # Only fill in blanks from enrichment, don't overwrite existing
        for key, val in enriched.items():
            if val and not metadata.get(key):
                metadata[key] = val

    # Try to fetch and embed artwork
    if not metadata.get('has_artwork'):
        try:
            from .artwork import fetch_artwork, embed_artwork
            image_bytes = fetch_artwork(
                metadata.get('artist', ''),
                metadata.get('title', ''),
                metadata.get('label', ''),
                metadata.get('catalog_number', ''),
            )
            if image_bytes:
                embed_artwork(filepath, image_bytes)
                metadata['has_artwork'] = True
        except Exception as e:
            logger.warning(f"Artwork fetch/embed failed: {e}")

    # Clean up year and catalog number
    if metadata.get('year'):
        metadata['year'] = _clean_year(metadata['year'])
    if metadata.get('catalog_number'):
        metadata['catalog_number'] = _clean_catalog_number(metadata['catalog_number'])
    if metadata.get('genre'):
        metadata['genre'] = _clean_genre(metadata['genre'])

    # Normalize the DB fields as well as embedded tags. Previously
    # write_tags cleaned a copy but PipelineItem kept the raw title, so the
    # renamer received strings like "Album - 01 Track" and preserved release
    # page annotations in the filename.
    metadata = _clean_metadata(metadata)

    # Write tags to file
    write_tags(filepath, metadata)

    # Update pipeline item
    pipeline_item.artist = metadata.get('artist', pipeline_item.artist)
    pipeline_item.title = metadata.get('title', pipeline_item.title)
    pipeline_item.album = metadata.get('album', pipeline_item.album)
    pipeline_item.label = metadata.get('label', pipeline_item.label)
    pipeline_item.catalog_number = metadata.get('catalog_number', pipeline_item.catalog_number)
    pipeline_item.genre = metadata.get('genre', pipeline_item.genre)
    pipeline_item.year = metadata.get('year', pipeline_item.year)
    pipeline_item.track_number = metadata.get('track_number', pipeline_item.track_number)
    pipeline_item.has_artwork = metadata.get('has_artwork', False)
    pipeline_item.metadata_source = source
    pipeline_item.save(update_fields=[
        'artist', 'title', 'album', 'label', 'catalog_number',
        'genre', 'year', 'track_number', 'has_artwork', 'metadata_source',
    ])
