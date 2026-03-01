import os
import shutil
import logging
import threading

from django.conf import settings
from django import db

logger = logging.getLogger(__name__)

STAGE_FOLDERS = {
    'downloaded': '01_downloaded',
    'tagged': '02_tagged',
    'renamed': '03_renamed',
    'ready': '04_ready',
}


def get_pipeline_root():
    return getattr(settings, 'SOULSEEK_DOWNLOAD_ROOT', '/music/soulseek')


def ensure_pipeline_folders():
    root = get_pipeline_root()
    for folder in STAGE_FOLDERS.values():
        os.makedirs(os.path.join(root, folder), exist_ok=True)


def _find_file_on_disk(root, basename):
    """Walk the download root looking for a file by basename."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip our pipeline stage folders
        rel = os.path.relpath(dirpath, root)
        if rel.split(os.sep)[0] in STAGE_FOLDERS.values():
            continue
        if basename in filenames:
            return os.path.join(dirpath, basename)
    return None


def reconstruct_download_path(download):
    """Build the expected local path for a slskd download.

    slskd saves files as: {download_root}/{username or subpath}/{filename}
    Remote path comes from Windows with backslashes. The exact directory
    structure varies, so we try the canonical path first then search.
    """
    root = get_pipeline_root()
    filename = download.filename.replace('\\', '/')
    basename = os.path.basename(filename)

    # Try canonical: root/username/full_remote_path
    canonical = os.path.join(root, download.username, filename)
    if os.path.exists(canonical):
        return canonical

    # Try just root/last_two_parts (slskd often flattens the path)
    parts = filename.split('/')
    for n in range(1, min(len(parts) + 1, 4)):
        candidate = os.path.join(root, *parts[-n:])
        if os.path.exists(candidate):
            return candidate

    # Fallback: search the download root for the basename
    found = _find_file_on_disk(root, basename)
    if found:
        return found

    # Give up, return canonical so caller gets a useful error path
    return canonical


def _move_to_stage(filepath, stage):
    """Move a file to a pipeline stage folder. Returns new path."""
    root = get_pipeline_root()
    ensure_pipeline_folders()
    dest_folder = os.path.join(root, STAGE_FOLDERS[stage])
    basename = os.path.basename(filepath)
    dest_path = os.path.join(dest_folder, basename)

    # Handle name collision
    if os.path.exists(dest_path) and dest_path != filepath:
        name, ext = os.path.splitext(basename)
        counter = 1
        while os.path.exists(dest_path):
            dest_path = os.path.join(dest_folder, f"{name}_{counter}{ext}")
            counter += 1

    if filepath != dest_path:
        shutil.move(filepath, dest_path)
    return dest_path


def discover_and_ingest(download_id):
    """Find a completed download on disk, move to 01_downloaded, create PipelineItem."""
    from organize.models import PipelineItem
    from soulseek.models import Download

    dl = Download.objects.get(id=download_id)

    # Skip if already ingested
    if PipelineItem.objects.filter(download=dl).exists():
        return None

    # Find the file
    filepath = dl.local_path or reconstruct_download_path(dl)
    if not os.path.exists(filepath):
        logger.warning(f"File not found for download {dl.id}: {filepath}")
        return None

    # Update download.local_path if not set
    if not dl.local_path:
        dl.local_path = filepath
        dl.save(update_fields=['local_path'])

    # Move to 01_downloaded
    new_path = _move_to_stage(filepath, 'downloaded')

    # Pre-fill metadata from WantedItem if available
    wanted = dl.wanted_item
    item = PipelineItem.objects.create(
        download=dl,
        wanted_item=wanted,
        original_filename=os.path.basename(filepath),
        current_path=new_path,
        artist=wanted.artist if wanted else '',
        title=wanted.title if wanted else '',
        album=wanted.release_name if wanted else '',
        label=wanted.label if wanted else '',
        catalog_number=wanted.catalog_number if wanted else '',
        stage='downloaded',
    )
    return item


def scan_completed_downloads():
    """Scan all completed Downloads and create PipelineItems for untracked ones."""
    from organize.models import PipelineItem
    from soulseek.models import Download

    ensure_pipeline_folders()
    completed = Download.objects.filter(status='completed')
    already_tracked = set(
        PipelineItem.objects.values_list('download_id', flat=True)
    )

    created = 0
    for dl in completed:
        if dl.id in already_tracked:
            continue
        try:
            item = discover_and_ingest(dl.id)
            if item:
                created += 1
        except Exception as e:
            logger.error(f"Error ingesting download {dl.id}: {e}")

    return created


def process_pipeline_item(item_id):
    """Process a single item through all pipeline stages."""
    try:
        from organize.models import PipelineItem
        from .tagger import tag_file
        from .renamer import rename_file

        item = PipelineItem.objects.get(id=item_id)

        # Stage 1: Tag
        if item.stage == 'downloaded':
            item.stage = 'tagging'
            item.save(update_fields=['stage'])

            try:
                tag_file(item)
                item.refresh_from_db()
                new_path = _move_to_stage(item.current_path, 'tagged')
                item.current_path = new_path
                item.stage = 'tagged'
                item.save(update_fields=['current_path', 'stage'])

                # Update WantedItem status
                if item.wanted_item:
                    item.wanted_item.status = 'tagged'
                    item.wanted_item.save(update_fields=['status'])
            except Exception as e:
                logger.error(f"Tagging failed for item {item_id}: {e}")
                item.stage = 'failed'
                item.error_message = f"Tagging failed: {e}"
                item.save(update_fields=['stage', 'error_message'])
                return

        # Stage 2: Rename
        if item.stage == 'tagged':
            item.stage = 'renaming'
            item.save(update_fields=['stage'])

            try:
                rename_file(item)
                item.refresh_from_db()
                new_path = _move_to_stage(item.current_path, 'renamed')
                item.current_path = new_path
                item.stage = 'renamed'
                item.save(update_fields=['current_path', 'stage'])
            except Exception as e:
                logger.error(f"Renaming failed for item {item_id}: {e}")
                item.stage = 'failed'
                item.error_message = f"Renaming failed: {e}"
                item.save(update_fields=['stage', 'error_message'])
                return

        # Stage 3: Move to ready
        if item.stage == 'renamed':
            new_path = _move_to_stage(item.current_path, 'ready')
            item.current_path = new_path
            item.stage = 'ready'
            item.save(update_fields=['current_path', 'stage'])

            # Update WantedItem status
            if item.wanted_item:
                item.wanted_item.status = 'organized'
                item.wanted_item.save(update_fields=['status'])
    except Exception as e:
        logger.error(f"Pipeline processing failed for item {item_id}: {e}")
        try:
            from organize.models import PipelineItem
            item = PipelineItem.objects.get(id=item_id)
            item.stage = 'failed'
            item.error_message = str(e)
            item.save(update_fields=['stage', 'error_message'])
        except Exception:
            pass
    finally:
        db.connections.close_all()


def process_all_pending():
    """Process all items in 'downloaded' stage sequentially."""
    try:
        from organize.models import PipelineItem
        items = PipelineItem.objects.filter(stage='downloaded')
        for item in items:
            process_pipeline_item(item.id)
    finally:
        db.connections.close_all()


def auto_ingest_download(download_id):
    """Auto-trigger: discover file, create PipelineItem, process through pipeline."""
    try:
        item = discover_and_ingest(download_id)
        if item:
            process_pipeline_item(item.id)
    except Exception as e:
        logger.error(f"Auto-ingest failed for download {download_id}: {e}")
    finally:
        db.connections.close_all()
