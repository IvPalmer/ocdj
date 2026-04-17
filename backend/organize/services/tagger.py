import os
import logging

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


def write_tags(filepath, metadata):
    """Write tags to an audio file using mutagen. Format-aware."""
    try:
        audio = mutagen.File(filepath)
        if audio is None:
            logger.warning(f"Cannot identify audio format: {filepath}")
            return

        ext = os.path.splitext(filepath)[1].lower()

        if ext in ('.mp3', '.aiff', '.aif'):
            # ID3 tags
            if audio.tags is None:
                if ext == '.mp3':
                    audio = MP3(filepath)
                    audio.add_tags()
                elif ext in ('.aiff', '.aif'):
                    audio = AIFF(filepath)
                    audio.add_tags()

            tags = audio.tags
            if metadata.get('artist'):
                tags['TPE1'] = TPE1(encoding=3, text=[metadata['artist']])
            if metadata.get('title'):
                tags['TIT2'] = TIT2(encoding=3, text=[metadata['title']])
            if metadata.get('album'):
                tags['TALB'] = TALB(encoding=3, text=[metadata['album']])
            if metadata.get('genre'):
                tags['TCON'] = TCON(encoding=3, text=[metadata['genre']])
            if metadata.get('year'):
                tags['TDRC'] = TDRC(encoding=3, text=[metadata['year']])
            if metadata.get('track_number'):
                tags['TRCK'] = TRCK(encoding=3, text=[metadata['track_number']])
            if metadata.get('label'):
                tags['TPUB'] = TPUB(encoding=3, text=[metadata['label']])
            if metadata.get('catalog_number'):
                tags.add(TXXX(encoding=3, desc='CATALOGNUMBER', text=[metadata['catalog_number']]))

            audio.save()

        elif ext == '.flac':
            flac = FLAC(filepath)
            if metadata.get('artist'):
                flac['artist'] = metadata['artist']
            if metadata.get('title'):
                flac['title'] = metadata['title']
            if metadata.get('album'):
                flac['album'] = metadata['album']
            if metadata.get('genre'):
                flac['genre'] = metadata['genre']
            if metadata.get('year'):
                flac['date'] = metadata['year']
            if metadata.get('track_number'):
                flac['tracknumber'] = metadata['track_number']
            if metadata.get('label'):
                flac['label'] = metadata['label']
            if metadata.get('catalog_number'):
                flac['catalognumber'] = metadata['catalog_number']

            flac.save()

        else:
            # Use easy tags as fallback for other formats
            audio = mutagen.File(filepath, easy=True)
            if audio is not None:
                if metadata.get('artist'):
                    audio['artist'] = metadata['artist']
                if metadata.get('title'):
                    audio['title'] = metadata['title']
                if metadata.get('album'):
                    audio['album'] = metadata['album']
                if metadata.get('genre'):
                    audio['genre'] = metadata['genre']
                if metadata.get('year'):
                    audio['date'] = metadata['year']
                audio.save()
    except Exception as e:
        logger.error(f"Error writing tags to {filepath}: {e}")
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
