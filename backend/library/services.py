import os
import logging

import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3

from django.db.models import Count, Sum

from core.services.config import get_config

from .models import LibraryTrack

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.aiff', '.aif', '.wav', '.ogg', '.m4a'}

EXT_TO_FORMAT = {
    '.mp3': 'mp3',
    '.flac': 'flac',
    '.aiff': 'aiff',
    '.aif': 'aiff',
    '.wav': 'wav',
    '.ogg': 'ogg',
    '.m4a': 'm4a',
}


def _get_ready_dir():
    """Get the ready directory where finished files live."""
    return os.path.join(get_config('SOULSEEK_DOWNLOAD_ROOT'), '05_ready')


def _read_audio_metadata(filepath):
    """Read metadata and technical info from an audio file."""
    result = {
        'artist': '',
        'title': '',
        'album': '',
        'label': '',
        'catalog_number': '',
        'genre': '',
        'year': '',
        'bitrate': None,
        'sample_rate': None,
        'duration_seconds': None,
        'has_artwork': False,
    }

    try:
        # Read tags via easy interface
        audio_easy = mutagen.File(filepath, easy=True)
        if audio_easy:
            result['artist'] = (audio_easy.get('artist') or [''])[0]
            result['title'] = (audio_easy.get('title') or [''])[0]
            result['album'] = (audio_easy.get('album') or [''])[0]
            result['genre'] = (audio_easy.get('genre') or [''])[0]
            result['year'] = (audio_easy.get('date') or [''])[0]

        # Read technical info from raw file
        audio = mutagen.File(filepath)
        if audio:
            if audio.info:
                result['duration_seconds'] = getattr(audio.info, 'length', None)
                result['sample_rate'] = getattr(audio.info, 'sample_rate', None)

                bitrate = getattr(audio.info, 'bitrate', None)
                if bitrate:
                    result['bitrate'] = bitrate // 1000  # Convert to kbps

            # Check for artwork
            if hasattr(audio, 'pictures') and audio.pictures:
                result['has_artwork'] = True
            elif hasattr(audio, 'tags') and audio.tags:
                for key in audio.tags:
                    if 'APIC' in str(key):
                        result['has_artwork'] = True
                        break

        # Read label/catalog from raw tags (not in easy interface)
        raw = mutagen.File(filepath)
        if raw and hasattr(raw, 'tags') and raw.tags:
            ext = os.path.splitext(filepath)[1].lower()
            if ext in ('.mp3', '.aiff', '.aif'):
                # ID3 tags
                tpub = raw.tags.get('TPUB')
                if tpub:
                    result['label'] = str(tpub)
                for key, val in raw.tags.items():
                    if 'TXXX' in key and hasattr(val, 'desc'):
                        if val.desc.upper() == 'CATALOGNUMBER':
                            result['catalog_number'] = str(val)
            elif ext == '.flac':
                result['label'] = (raw.get('label') or [''])[0] if hasattr(raw, 'get') else ''
                result['catalog_number'] = (raw.get('catalognumber') or [''])[0] if hasattr(raw, 'get') else ''

    except Exception as e:
        logger.warning(f"Error reading metadata from {filepath}: {e}")

    return result


def scan_library():
    """Walk the ready directory, read metadata, create/update LibraryTrack records.

    Incremental: skips files already in DB with matching mtime.
    Marks missing files.
    """
    ready_dir = _get_ready_dir()
    if not os.path.isdir(ready_dir):
        logger.warning(f"Ready directory does not exist: {ready_dir}")
        return {'created': 0, 'updated': 0, 'missing': 0, 'skipped': 0}

    created = 0
    updated = 0
    skipped = 0
    seen_paths = set()

    for dirpath, _, filenames in os.walk(ready_dir):
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue

            filepath = os.path.join(dirpath, filename)
            seen_paths.add(filepath)

            try:
                stat = os.stat(filepath)
                mtime = stat.st_mtime
                file_size = stat.st_size
            except OSError:
                continue

            # Check if already tracked with same mtime
            existing = LibraryTrack.objects.filter(file_path=filepath).first()
            if existing and existing.file_mtime == mtime:
                # Un-mark missing if it was previously marked
                if existing.missing:
                    existing.missing = False
                    existing.save(update_fields=['missing'])
                skipped += 1
                continue

            # Read metadata
            meta = _read_audio_metadata(filepath)
            fmt = EXT_TO_FORMAT.get(ext, '')

            defaults = {
                'file_mtime': mtime,
                'artist': meta['artist'],
                'title': meta['title'],
                'album': meta['album'],
                'label': meta['label'],
                'catalog_number': meta['catalog_number'],
                'genre': meta['genre'],
                'year': meta['year'],
                'format': fmt,
                'bitrate': meta['bitrate'],
                'sample_rate': meta['sample_rate'],
                'duration_seconds': meta['duration_seconds'],
                'file_size_bytes': file_size,
                'has_artwork': meta['has_artwork'],
                'missing': False,
            }

            _, was_created = LibraryTrack.objects.update_or_create(
                file_path=filepath,
                defaults=defaults,
            )

            if was_created:
                created += 1
            else:
                updated += 1

    # Mark files not seen on disk as missing
    missing_count = LibraryTrack.objects.filter(missing=False).exclude(
        file_path__in=seen_paths
    ).update(missing=True)

    return {
        'created': created,
        'updated': updated,
        'missing': missing_count,
        'skipped': skipped,
    }


def get_track_metadata(file_path):
    """Read full metadata from a single file."""
    return _read_audio_metadata(file_path)


def update_track_metadata(track, data):
    """Write metadata changes to file and update DB record."""
    from organize.services.tagger import write_tags

    filepath = track.file_path
    write_tags(filepath, data)

    # Update DB record
    for key, val in data.items():
        if hasattr(track, key):
            setattr(track, key, val)
    track.save()

    return track


def get_library_stats():
    """Library statistics."""
    qs = LibraryTrack.objects.filter(missing=False)
    total = qs.count()

    format_counts = dict(
        qs.values_list('format')
        .annotate(count=Count('id'))
        .values_list('format', 'count')
    )

    genre_counts = list(
        qs.exclude(genre='')
        .values('genre')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    total_size = qs.aggregate(total=Sum('file_size_bytes'))['total'] or 0

    return {
        'total_tracks': total,
        'by_format': format_counts,
        'top_genres': genre_counts,
        'total_size_bytes': total_size,
    }
