import os
import re
import logging

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE = '{artist} - {title}'


def sanitize_filename(name):
    """Clean up a filename: strip empty brackets, invalid chars, collapse whitespace."""
    # Remove empty brackets/parens
    name = re.sub(r'\[\s*\]', '', name)
    name = re.sub(r'\(\s*\)', '', name)

    # Remove invalid filesystem chars
    name = re.sub(r'[<>:"/\\|?*]', '', name)

    # Collapse multiple spaces
    name = re.sub(r'\s+', ' ', name).strip()

    # Remove trailing dots/spaces (Windows compat)
    name = name.rstrip('. ')

    return name


def rename_file(pipeline_item):
    """Rename a file based on the configured template and metadata."""
    from core.views import get_config

    template = get_config('ORGANIZE_RENAME_TEMPLATE') or DEFAULT_TEMPLATE

    # Build template vars
    vars = {
        'artist': pipeline_item.artist or 'Unknown Artist',
        'title': pipeline_item.title or 'Unknown Title',
        'album': pipeline_item.album or '',
        'label': pipeline_item.label or '',
        'catalog': pipeline_item.catalog_number or '',
        'genre': pipeline_item.genre or '',
        'year': pipeline_item.year or '',
        'track': pipeline_item.track_number or '',
    }

    # Apply template
    try:
        new_name = template.format(**vars)
    except KeyError as e:
        logger.warning(f"Invalid template variable {e}, using default")
        new_name = DEFAULT_TEMPLATE.format(**vars)

    new_name = sanitize_filename(new_name)

    if not new_name:
        new_name = sanitize_filename(pipeline_item.original_filename)

    # Preserve extension
    _, ext = os.path.splitext(pipeline_item.current_path)
    new_filename = f"{new_name}{ext}"

    # Rename on disk
    current_dir = os.path.dirname(pipeline_item.current_path)
    new_path = os.path.join(current_dir, new_filename)

    # Handle collision
    if os.path.exists(new_path) and new_path != pipeline_item.current_path:
        counter = 1
        while os.path.exists(new_path):
            new_path = os.path.join(current_dir, f"{new_name}_{counter}{ext}")
            counter += 1

    if pipeline_item.current_path != new_path:
        os.rename(pipeline_item.current_path, new_path)

    pipeline_item.current_path = new_path
    pipeline_item.final_filename = os.path.basename(new_path)
    pipeline_item.save(update_fields=['current_path', 'final_filename'])
