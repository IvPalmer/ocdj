import os
import re
import shutil
import subprocess
import logging

import mutagen

logger = logging.getLogger(__name__)

DEFAULT_RULES = """wav -> aiff
flac -> aiff
mp3>=320k -> keep
mp3<320k -> skip
aiff -> keep
* -> keep"""

# Map of format name -> file extensions
FORMAT_EXTENSIONS = {
    'wav': ['.wav'],
    'flac': ['.flac'],
    'mp3': ['.mp3'],
    'aiff': ['.aiff', '.aif'],
    'ogg': ['.ogg'],
    'm4a': ['.m4a'],
    'wma': ['.wma'],
}

# Reverse lookup: extension -> format name
EXT_TO_FORMAT = {}
for fmt, exts in FORMAT_EXTENSIONS.items():
    for ext in exts:
        EXT_TO_FORMAT[ext] = fmt

# FFmpeg codec args per output format
FFMPEG_CODEC_ARGS = {
    'aiff': ['-c:a', 'pcm_s16be'],
    'wav': ['-c:a', 'pcm_s16le'],
    'flac': ['-c:a', 'flac'],
    'mp3': ['-c:a', 'libmp3lame', '-b:a', '320k'],
}

# Canonical extension per format
FORMAT_TO_EXT = {
    'wav': '.wav',
    'flac': '.flac',
    'mp3': '.mp3',
    'aiff': '.aiff',
    'ogg': '.ogg',
    'm4a': '.m4a',
}


def _get_bitrate(filepath):
    """Get the bitrate of an audio file in kbps using mutagen."""
    try:
        audio = mutagen.File(filepath)
        if audio and hasattr(audio.info, 'bitrate'):
            return audio.info.bitrate // 1000
    except Exception:
        pass
    return 0


def parse_rules(rules_text):
    """Parse conversion rules DSL into a list of rule dicts.

    Rule format examples:
        wav -> aiff
        flac -> aiff
        mp3>=320k -> keep
        mp3<320k -> skip
        aiff -> keep
        * -> keep
    """
    rules = []
    for line in rules_text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        match = re.match(
            r'^(\*|[a-zA-Z0-9]+)'        # source format or wildcard
            r'(?:(>=|<=|>|<|=)(\d+)k?)?'  # optional bitrate condition
            r'\s*->\s*'                    # arrow
            r'(\w+)$',                     # target: format name, 'keep', or 'skip'
            line,
        )
        if not match:
            logger.warning(f"Invalid conversion rule: {line}")
            continue

        source_fmt = match.group(1).lower()
        bitrate_op = match.group(2)
        bitrate_val = int(match.group(3)) if match.group(3) else None
        target = match.group(4).lower()

        rules.append({
            'source': source_fmt,
            'bitrate_op': bitrate_op,
            'bitrate_val': bitrate_val,
            'target': target,
        })

    return rules


def _bitrate_matches(actual_bitrate, op, threshold):
    """Check if actual bitrate satisfies the condition."""
    if op == '>=':
        return actual_bitrate >= threshold
    if op == '<=':
        return actual_bitrate <= threshold
    if op == '>':
        return actual_bitrate > threshold
    if op == '<':
        return actual_bitrate < threshold
    if op == '=':
        return actual_bitrate == threshold
    return False


def match_rule(filepath, rules):
    """Find the first matching rule for a file. Returns the rule dict or None."""
    ext = os.path.splitext(filepath)[1].lower()
    source_fmt = EXT_TO_FORMAT.get(ext, '')
    bitrate = None  # lazy-loaded

    for rule in rules:
        # Check format match
        if rule['source'] != '*' and rule['source'] != source_fmt:
            continue

        # Check bitrate condition if present
        if rule['bitrate_op'] and rule['bitrate_val'] is not None:
            if bitrate is None:
                bitrate = _get_bitrate(filepath)
            if not _bitrate_matches(bitrate, rule['bitrate_op'], rule['bitrate_val']):
                continue

        return rule

    return None


def _read_all_tags(filepath):
    """Read all tags and artwork from a file for re-application after conversion."""
    from .tagger import read_existing_tags
    tags = read_existing_tags(filepath)

    # Also extract artwork bytes for re-embedding
    artwork_data = None
    try:
        audio = mutagen.File(filepath)
        if audio:
            # FLAC
            if hasattr(audio, 'pictures') and audio.pictures:
                pic = audio.pictures[0]
                artwork_data = (pic.data, pic.mime)
            # ID3 (MP3, AIFF)
            elif hasattr(audio, 'tags') and audio.tags:
                for key in audio.tags:
                    if 'APIC' in str(key):
                        frame = audio.tags[key]
                        artwork_data = (frame.data, frame.mime)
                        break
    except Exception as e:
        logger.warning(f"Could not extract artwork from {filepath}: {e}")

    return tags, artwork_data


def _embed_artwork_to_file(filepath, artwork_data):
    """Embed artwork into a converted file."""
    if not artwork_data:
        return
    image_bytes, mime_type = artwork_data
    try:
        from .artwork import embed_artwork
        embed_artwork(filepath, image_bytes)
    except Exception as e:
        logger.warning(f"Could not re-embed artwork to {filepath}: {e}")


def convert_file(source_path, target_format):
    """Convert an audio file to target format using FFmpeg.

    Returns the path to the converted file (in the same directory).
    """
    if target_format not in FFMPEG_CODEC_ARGS:
        raise ValueError(f"Unsupported target format: {target_format}")

    target_ext = FORMAT_TO_EXT[target_format]
    base = os.path.splitext(source_path)[0]
    target_path = base + target_ext

    # Handle collision
    if os.path.exists(target_path) and target_path != source_path:
        counter = 1
        while os.path.exists(target_path):
            target_path = f"{base}_{counter}{target_ext}"
            counter += 1

    codec_args = FFMPEG_CODEC_ARGS[target_format]

    cmd = [
        'ffmpeg',
        '-i', source_path,
        *codec_args,
        '-y',
        target_path,
    ]

    logger.info(f"Converting {os.path.basename(source_path)} -> {target_format}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg conversion failed (exit {result.returncode}): {result.stderr[-500:]}"
        )

    if not os.path.exists(target_path):
        raise RuntimeError(f"FFmpeg completed but output file not found: {target_path}")

    return target_path


def convert_pipeline_item(pipeline_item):
    """Run format conversion on a pipeline item.

    Reads conversion rules from config, matches the file, converts if needed,
    re-applies tags, and updates the pipeline item.
    """
    from core.views import get_config

    rules_text = get_config('ORGANIZE_CONVERSION_RULES') or DEFAULT_RULES
    rules = parse_rules(rules_text)

    filepath = pipeline_item.current_path
    rule = match_rule(filepath, rules)

    if rule is None:
        # No rule matched — treat as keep
        logger.info(f"No conversion rule matched for {filepath}, keeping as-is")
        return

    target = rule['target']

    if target == 'skip':
        raise ValueError(
            f"File skipped by conversion rules: "
            f"{os.path.basename(filepath)} ({EXT_TO_FORMAT.get(os.path.splitext(filepath)[1].lower(), 'unknown')})"
        )

    if target == 'keep':
        logger.info(f"Keeping format for {os.path.basename(filepath)}")
        return

    # Check if file is already in the target format
    ext = os.path.splitext(filepath)[1].lower()
    source_fmt = EXT_TO_FORMAT.get(ext, '')
    if source_fmt == target:
        logger.info(f"File already in {target} format, no conversion needed")
        return

    # Read tags and artwork before conversion
    tags, artwork_data = _read_all_tags(filepath)

    # Convert
    converted_path = convert_file(filepath, target)

    # Re-apply tags to converted file
    from .tagger import write_tags
    tag_metadata = {
        'artist': tags.get('artist', pipeline_item.artist),
        'title': tags.get('title', pipeline_item.title),
        'album': tags.get('album', pipeline_item.album),
        'genre': tags.get('genre', pipeline_item.genre),
        'year': tags.get('date', pipeline_item.year),
        'track_number': tags.get('tracknumber', pipeline_item.track_number),
        'label': pipeline_item.label,
        'catalog_number': pipeline_item.catalog_number,
    }
    write_tags(converted_path, tag_metadata)

    # Re-embed artwork
    _embed_artwork_to_file(converted_path, artwork_data)

    # Remove original file (it's been replaced)
    if os.path.exists(filepath) and filepath != converted_path:
        os.remove(filepath)

    # Update pipeline item
    pipeline_item.current_path = converted_path
    pipeline_item.final_filename = os.path.basename(converted_path)
    pipeline_item.save(update_fields=['current_path', 'final_filename'])
