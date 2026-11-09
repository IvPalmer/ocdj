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

SKIPPABLE_STAGE_ORDER = ['downloaded', 'tagged', 'renamed', 'converted', 'ready']

ACTIVE_STAGE_TO_DONE = {
    'tagging': 'tagged',
    'renaming': 'renamed',
    'converting': 'converted',
}


def get_pipeline_root():
    return get_config('SOULSEEK_DOWNLOAD_ROOT')


def ensure_pipeline_folders():
    root = get_pipeline_root()
    for folder in STAGE_FOLDERS.values():
        os.makedirs(os.path.join(root, folder), exist_ok=True)


def stage_folder_path(stage):
    """Return the absolute folder for a durable pipeline stage.

    The stage names are the database vocabulary; the numbered folders are an
    operator-facing workbench. Keeping the mapping here prevents views and
    services from quietly diverging on where a file should live.
    """
    return os.path.join(get_pipeline_root(), STAGE_FOLDERS[stage])


def _available_path(dest_folder, basename):
    """Return a non-conflicting destination path inside dest_folder."""
    dest_path = os.path.join(dest_folder, basename)
    if not os.path.exists(dest_path):
        return dest_path

    name, ext = os.path.splitext(basename)
    counter = 1
    while os.path.exists(dest_path):
        dest_path = os.path.join(dest_folder, f"{name}_{counter}{ext}")
        counter += 1
    return dest_path


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
    ensure_pipeline_folders()
    dest_folder = stage_folder_path(stage)
    basename = os.path.basename(filepath)
    dest_path = os.path.join(dest_folder, basename)
    if os.path.exists(dest_path) and dest_path != filepath:
        dest_path = _available_path(dest_folder, basename)

    if filepath != dest_path:
        shutil.move(filepath, dest_path)
    return dest_path


def move_item_to_stage(item, stage):
    """Move an item file to a durable stage folder and persist that stage."""
    item.current_path = _move_to_stage(item.current_path, stage)
    item.stage = stage
    item.save(update_fields=['current_path', 'stage'])
    return item


def write_uploaded_file_to_downloaded(uploaded_file):
    """Store an uploaded audio file in 01_downloaded using pipeline collision rules."""
    ensure_pipeline_folders()
    dest_path = _available_path(
        stage_folder_path('downloaded'),
        os.path.basename(uploaded_file.name),
    )
    with open(dest_path, 'wb') as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)
    return dest_path


def completed_stage_for(stage):
    """Normalize transient working stages before calculating manual skips."""
    return ACTIVE_STAGE_TO_DONE.get(stage, stage)


def next_skippable_stage(stage):
    """Return the next manual-skip target, or None when skipping is invalid."""
    current_base = completed_stage_for(stage)
    if current_base not in SKIPPABLE_STAGE_ORDER:
        return None
    idx = SKIPPABLE_STAGE_ORDER.index(current_base)
    if idx >= len(SKIPPABLE_STAGE_ORDER) - 1:
        return None
    return SKIPPABLE_STAGE_ORDER[idx + 1]


def _update_wanted_status(item, wanted_status):
    if item.wanted_item and wanted_status:
        item.wanted_item.status = wanted_status
        item.wanted_item.save(update_fields=['status'])


def _mark_failed(item, label, exc):
    item.stage = 'failed'
    item.error_message = f"{label} failed: {exc}"
    item.save(update_fields=['stage', 'error_message'])


def _run_stage_step(item, *, source_stage, working_stage, done_stage, label, action,
                    wanted_status=None):
    """Run one service-owned stage and make the disk move match the DB stage.

    Tagging, renaming, and conversion each mutate file metadata or filename in
    place, then the pipeline moves the result to the next numbered folder. This
    helper keeps that contract explicit and identical across stages.
    """
    if item.stage != source_stage:
        return True

    item.stage = working_stage
    item.save(update_fields=['stage'])

    try:
        action(item)
        item.refresh_from_db()
        move_item_to_stage(item, done_stage)
        _update_wanted_status(item, wanted_status)
        return True
    except Exception as e:
        logger.error(f"{label} failed for item {item.id}: {e}")
        _mark_failed(item, label, e)
        return False


def _staged_candidates(download):
    root = stage_folder_path('downloaded')
    parts = download.filename.replace(chr(92), '/').split('/')[-2:]
    if any(part in ('', '.', '..') for part in parts):
        return set()
    return {os.path.join(root, *parts), os.path.join(root, parts[-1])}


def _staged_download_path(download, expected_size):
    """Resolve slskd's staged layout with an exact suffix and transfer size."""
    if not expected_size:
        return None
    root = stage_folder_path('downloaded')
    matches = []
    for path in _staged_candidates(download):
        if os.path.realpath(path) != os.path.join(os.path.realpath(root), os.path.relpath(path, root)):
            continue
        try:
            if os.path.isfile(path) and os.path.getsize(path) == expected_size:
                matches.append(path)
        except FileNotFoundError:
            continue  # Another pipeline action moved it.
    return matches[0] if len(matches) == 1 else None


def _staged_transfer_evidence(downloads):
    """Check transfer provenance before an orphan scan can move Soulseek bytes."""
    from soulseek.services import SlskdClient
    import requests

    candidates = {dl.pk: {p for p in _staged_candidates(dl) if os.path.isfile(p)} for dl in downloads}
    if not any(candidates.values()):
        return {}, set()
    try:
        users = SlskdClient().get_downloads()
    except requests.RequestException:
        # Defer known candidates until slskd can confirm the association.
        return {}, set().union(*candidates.values())
    transfers = {}
    for user in users:
        for directory in user.get('directories', []):
            for file in directory.get('files', []):
                key = (user['username'].casefold(), file['filename'].casefold())
                transfers.setdefault(key, []).append(file)

    proven = {}
    deferred = set()
    for dl in downloads:
        paths = candidates[dl.pk]
        if not paths:
            continue
        found = transfers.get((dl.username.casefold(), dl.filename.casefold()), [])
        if dl.slskd_id:
            found = [f for f in found if f.get('id') == dl.slskd_id]
        if len(found) != 1:
            if found:
                deferred.update(paths)
            continue
        flags = {part.strip() for part in (found[0].get('state') or '').split(',')}
        if 'Completed' not in flags:
            deferred.update(paths)
        elif 'Succeeded' in flags:
            size = found[0].get('size')
            path = _staged_download_path(dl, size)
            if path:
                proven.setdefault(path, []).append((dl.pk, size))
    sizes = {}
    for path, claims in proven.items():
        deferred.add(path)
        if len(claims) == 1:
            pk, size = claims[0]
            sizes[pk] = size
    return sizes, deferred


def _lock_staged_path(path):
    # The orphan scan and completed-transfer ingest run in separate processes.
    if db.connection.vendor == 'postgresql':
        with db.connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(172808, hashtext(%s))', [path])


@db.transaction.atomic
def discover_and_ingest(download_id, expected_size=None):
    """Link a proven completed download to its staged pipeline item."""
    from organize.models import PipelineItem
    from soulseek.models import Download

    dl = Download.objects.select_for_update().get(id=download_id)

    # Skip if already ingested
    if PipelineItem.objects.filter(download=dl).exists():
        return None

    size = expected_size or (dl.search_result.file_size if dl.search_result_id else 0)
    staged = _staged_download_path(dl, size)
    filepath = dl.local_path or staged or reconstruct_download_path(dl)
    if not os.path.isfile(filepath):
        logger.warning("File not found for download %s", dl.id)
        return None

    if filepath == staged:
        _lock_staged_path(staged)
        existing = list(PipelineItem.objects.select_for_update().filter(current_path=staged)[:2])
        if existing:
            if len(existing) == 1 and existing[0].download_id is None:
                existing[0].download = dl
                if existing[0].wanted_item_id is None:
                    existing[0].wanted_item_id = dl.wanted_item_id
                existing[0].save(update_fields=['download', 'wanted_item'])
                dl.local_path = staged
                dl.save(update_fields=['local_path'])
            # Existing artifact stays in its current state, with its existing tags.
            return None
        if not os.path.isfile(staged):
            return None
        new_path = staged
    else:
        new_path = _move_to_stage(filepath, 'downloaded')
    if dl.local_path != new_path:
        dl.local_path = new_path
        dl.save(update_fields=['local_path'])

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

    Returns a summary dict, not a bare count. "Nothing was eligible",
    "everything was already tracked", "the file is gone from disk" and
    "ingest raised" all used to collapse into `created: 0`, which reads as a
    failed scan even when the scan worked perfectly.
    """
    from organize.models import PipelineItem
    from soulseek.models import Download

    ensure_pipeline_folders()

    # Pass 1 — Soulseek downloads
    downloads = list(Download.objects.filter(status__in=('queued', 'downloading', 'completed')))
    already_tracked_dl = set(
        PipelineItem.objects.values_list('download_id', flat=True)
    )
    sizes, deferred = _staged_transfer_evidence([dl for dl in downloads if dl.id not in already_tracked_dl])
    created_from_downloads = 0
    already_tracked = 0
    missing_files = 0
    errors = []
    for dl in downloads:
        size = sizes.get(dl.id)
        if dl.status != 'completed' and size is None:
            continue
        if dl.id in already_tracked_dl:
            already_tracked += 1
            continue
        try:
            item = discover_and_ingest(dl.id, expected_size=size) if size is not None else discover_and_ingest(dl.id)
            if item:
                created_from_downloads += 1
            elif PipelineItem.objects.filter(download=dl).exists():
                already_tracked += 1
            else:
                missing_files += 1
        except Exception as e:
            logger.error(f"Error ingesting download {dl.id}: {e}")
            errors.append({'download_id': dl.id, 'error': str(e)})

    # Pass 2 — orphan audio files in 01_downloaded/
    created_from_filesystem, fs_errors = _scan_filesystem_orphans(deferred)
    errors.extend(fs_errors)

    return {
        'created': created_from_downloads + created_from_filesystem,
        'created_from_downloads': created_from_downloads,
        'created_from_filesystem': created_from_filesystem,
        'already_tracked': already_tracked,
        'missing_files': missing_files,
        'errors': errors[:30],
        'error_count': len(errors),
    }


def _scan_filesystem_orphans(deferred):
    """Create PipelineItems for audio files in 01_downloaded/ that aren't tracked.

    Walks the stage folder recursively (handles `_to_triage/` and any
    slskd-style release subfolders). Each untracked audio file becomes a
    PipelineItem with download=None. The pipeline's tag/rename/convert
    stages don't depend on the Download FK, so they'll run normally.

    Returns (created, errors) so the caller can report failures instead of
    burying them in the log.
    """
    from organize.models import PipelineItem

    stage_root = os.path.join(get_pipeline_root(), STAGE_FOLDERS['downloaded'])
    if not os.path.isdir(stage_root):
        return 0, []

    tracked_paths = set(
        PipelineItem.objects.values_list('current_path', flat=True)
    )

    created = 0
    seen = 0
    errors = []
    for dirpath, _dirs, filenames in os.walk(stage_root):
        for fn in filenames:
            seen += 1
            if seen > _MAX_WALK_ENTRIES:
                logger.warning('_scan_filesystem_orphans: walk cap hit, stopping')
                errors.append({'path': stage_root, 'error': 'walk cap hit; scan incomplete'})
                return created, errors
            ext = os.path.splitext(fn)[1].lower()
            if ext not in AUDIO_EXTS:
                continue
            full = os.path.join(dirpath, fn)
            if full in tracked_paths or full in deferred:
                continue
            try:
                with db.transaction.atomic():
                    _lock_staged_path(full)
                    if not os.path.isfile(full) or PipelineItem.objects.filter(current_path=full).exists():
                        continue
                    PipelineItem.objects.create(
                        original_filename=fn, current_path=full, stage='downloaded',
                    )
                    created += 1
            except Exception as e:
                logger.error(f'Failed to create PipelineItem for {full}: {e}')
                errors.append({'path': full, 'error': str(e)})

    if created:
        logger.info(f'Ingested {created} orphan audio file(s) from {stage_root}')
    return created, errors


def process_pipeline_item(item_id):
    """Process a single item through all pipeline stages."""
    try:
        from organize.models import PipelineItem
        from .tagger import tag_file
        from .renamer import rename_file

        item = PipelineItem.objects.get(id=item_id)

        # Stage 1: Tag
        if not _run_stage_step(
            item,
            source_stage='downloaded',
            working_stage='tagging',
            done_stage='tagged',
            label='Tagging',
            action=tag_file,
            wanted_status='tagged',
        ):
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
        if not _run_stage_step(
            item,
            source_stage='tagged',
            working_stage='renaming',
            done_stage='renamed',
            label='Renaming',
            action=rename_file,
        ):
            return

        # Stage 3: Convert
        if item.stage == 'renamed':
            from .converter import convert_pipeline_item
            if not _run_stage_step(
                item,
                source_stage='renamed',
                working_stage='converting',
                done_stage='converted',
                label='Conversion',
                action=convert_pipeline_item,
            ):
                return

        # Stage 4: Move to ready
        if item.stage == 'converted':
            move_item_to_stage(item, 'ready')
            _update_wanted_status(item, 'organized')

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


def auto_ingest_download(download_id, expected_size=None):
    """Auto-trigger: discover file, create PipelineItem, process through pipeline."""
    try:
        item = discover_and_ingest(download_id, expected_size)
        if item:
            process_pipeline_item(item.id)
    except Exception as e:
        logger.error(f"Auto-ingest failed for download {download_id}: {e}")
    finally:
        db.connections.close_all()
