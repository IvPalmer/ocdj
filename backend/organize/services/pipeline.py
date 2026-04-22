import os
import shutil
import logging
import threading

from django import db

from core.services.config import get_config

logger = logging.getLogger(__name__)

# Guard against concurrent process_all_pending runs
_processing_all = False
_processing_all_lock = threading.Lock()

# Cap for disk-walk fallback to avoid multi-minute blocks on big/network mounts
_MAX_WALK_ENTRIES = 20000


def try_claim_processing_all():
    """Atomic check-and-set for the pipeline-wide processing guard.
    Returns True if the caller claimed the slot; False if a run is already in flight.
    """
    global _processing_all
    with _processing_all_lock:
        if _processing_all:
            return False
        _processing_all = True
        return True


def release_processing_all():
    global _processing_all
    with _processing_all_lock:
        _processing_all = False

STAGE_FOLDERS = {
    'downloaded': '01_downloaded',
    'tagged': '02_tagged',
    'renamed': '03_renamed',
    'converted': '04_converted',
    'ready': '05_ready',
    'published': '06_publish',
}


def get_pipeline_root():
    return get_config('SOULSEEK_DOWNLOAD_ROOT')


def ensure_pipeline_folders():
    root = get_pipeline_root()
    for folder in STAGE_FOLDERS.values():
        os.makedirs(os.path.join(root, folder), exist_ok=True)


def _find_file_on_disk(root, basename):
    """Walk the download root looking for a file by basename.

    Bails out after _MAX_WALK_ENTRIES inodes so we don't hang the request thread
    for minutes on huge / network-mounted trees.
    """
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip our pipeline stage folders
        rel = os.path.relpath(dirpath, root)
        if rel.split(os.sep)[0] in STAGE_FOLDERS.values():
            dirnames[:] = []
            continue
        scanned += len(filenames) + len(dirnames)
        if basename in filenames:
            return os.path.join(dirpath, basename)
        if scanned > _MAX_WALK_ENTRIES:
            logger.warning(
                f'_find_file_on_disk aborted after {scanned} entries looking for {basename!r}'
            )
            return None
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


AUDIO_EXTS = {'.mp3', '.flac', '.aiff', '.aif', '.wav', '.m4a', '.ogg'}


def scan_completed_downloads():
    """Scan for untracked files + create PipelineItems for them.

    Two passes:
      1. Download rows with status=completed that don't yet have a PipelineItem —
         re-uses the slskd-aware discover_and_ingest path.
      2. Filesystem sweep of 01_downloaded/ (and subdirs, including `_to_triage/`)
         for audio files that don't correspond to any PipelineItem.current_path.
         Creates a download-less PipelineItem so the file enters the pipeline.

    Pass 2 is what handles files that skipped Soulseek entirely — e.g.
    Telegram rips, friend shares, files rescued by audit_music_root.
    """
    from organize.models import PipelineItem
    from soulseek.models import Download

    ensure_pipeline_folders()

    # Pass 1 — Soulseek downloads
    completed = Download.objects.filter(status='completed')
    already_tracked_dl = set(
        PipelineItem.objects.values_list('download_id', flat=True)
    )
    created_from_downloads = 0
    for dl in completed:
        if dl.id in already_tracked_dl:
            continue
        try:
            item = discover_and_ingest(dl.id)
            if item:
                created_from_downloads += 1
        except Exception as e:
            logger.error(f"Error ingesting download {dl.id}: {e}")

    # Pass 2 — orphan audio files in 01_downloaded/
    created_from_filesystem = _scan_filesystem_orphans()

    return created_from_downloads + created_from_filesystem


def _scan_filesystem_orphans():
    """Create PipelineItems for audio files in 01_downloaded/ that aren't tracked.

    Walks the stage folder recursively (handles `_to_triage/` and any
    slskd-style release subfolders). Each untracked audio file becomes a
    PipelineItem with download=None. The pipeline's tag/rename/convert
    stages don't depend on the Download FK, so they'll run normally.
    """
    from organize.models import PipelineItem

    stage_root = os.path.join(get_pipeline_root(), STAGE_FOLDERS['downloaded'])
    if not os.path.isdir(stage_root):
        return 0

    tracked_paths = set(
        PipelineItem.objects.values_list('current_path', flat=True)
    )

    created = 0
    seen = 0
    for dirpath, _dirs, filenames in os.walk(stage_root):
        for fn in filenames:
            seen += 1
            if seen > _MAX_WALK_ENTRIES:
                logger.warning('_scan_filesystem_orphans: walk cap hit, stopping')
                return created
            ext = os.path.splitext(fn)[1].lower()
            if ext not in AUDIO_EXTS:
                continue
            full = os.path.join(dirpath, fn)
            if full in tracked_paths:
                continue
            try:
                PipelineItem.objects.create(
                    download=None,
                    wanted_item=None,
                    original_filename=fn,
                    current_path=full,
                    stage='downloaded',
                )
                created += 1
            except Exception as e:
                logger.error(f'Failed to create PipelineItem for {full}: {e}')

    if created:
        logger.info(f'Ingested {created} orphan audio file(s) from {stage_root}')
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

        # Stage 1.5: Agent-enrich if Discogs/MusicBrainz couldn't fix
        # garbage file tags. Gated on env (CLAUDE_CODE_OAUTH_TOKEN) and
        # a cheap heuristic so Opus isn't invoked on already-clean rows.
        if item.stage == 'tagged':
            try:
                from .agent_enrich import looks_like_garbage, enrich_pipeline_item
                if looks_like_garbage(item):
                    enriched = enrich_pipeline_item(item)
                    if enriched:
                        item.refresh_from_db()
            except Exception as e:
                # Never fail the pipeline because the agent step misbehaved.
                logger.warning(f'agent enrich skipped for item {item_id}: {e}')

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

        # Stage 3: Convert
        if item.stage == 'renamed':
            item.stage = 'converting'
            item.save(update_fields=['stage'])

            try:
                from .converter import convert_pipeline_item
                convert_pipeline_item(item)
                item.refresh_from_db()
                new_path = _move_to_stage(item.current_path, 'converted')
                item.current_path = new_path
                item.stage = 'converted'
                item.save(update_fields=['current_path', 'stage'])
            except Exception as e:
                logger.error(f"Conversion failed for item {item_id}: {e}")
                item.stage = 'failed'
                item.error_message = f"Conversion failed: {e}"
                item.save(update_fields=['stage', 'error_message'])
                return

        # Stage 4: Move to ready
        if item.stage == 'converted':
            new_path = _move_to_stage(item.current_path, 'ready')
            item.current_path = new_path
            item.stage = 'ready'
            item.save(update_fields=['current_path', 'stage'])

            # Update WantedItem status
            if item.wanted_item:
                item.wanted_item.status = 'organized'
                item.wanted_item.save(update_fields=['status'])

            # Stage 5: VPS mode — auto-publish into 06_publish/<id>/ so the
            # Mac drain daemon can fetch. No-op on Mac dev.
            if os.environ.get('OCDJ_AUTOPUBLISH') == '1':
                try:
                    from .publisher import publish_pipeline_item
                    publish_pipeline_item(item)
                except Exception as e:
                    logger.error(f'Auto-publish failed for item {item_id}: {e}')
                    # Leave item at stage=ready, archive_state=on_workbench.
                    # Publish can be retried manually via API.
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


def process_all_pending(already_claimed=False):
    """Process all items in 'downloaded' stage sequentially.

    Callers should normally claim the guard via try_claim_processing_all()
    BEFORE spawning a thread so the HTTP view can return 409 on contention
    without racing. Pass already_claimed=True to skip a re-claim here.
    """
    if not already_claimed:
        if not try_claim_processing_all():
            logger.info('process_all_pending: already running, skipping')
            return False

    try:
        from organize.models import PipelineItem
        items = PipelineItem.objects.filter(stage='downloaded')
        for item in items:
            process_pipeline_item(item.id)
    finally:
        release_processing_all()
        db.connections.close_all()
    return True


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
