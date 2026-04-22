"""Publish step: final transition from stage=ready to stage=published + archive_state=publishable.

Writes the VPS track ID into the file's grouping tag so the Mac drain daemon
can idempotently dedupe against Music.app library on retry. Computes sha256
for integrity verification during drain. Moves the file to 06_publish/<id>/
so deletion-on-archive is a clean rmdir of one directory.
"""

import hashlib
import logging
import os
import shutil
from datetime import timedelta

from django.utils import timezone
import mutagen
from mutagen.id3 import ID3, GRP1

from .pipeline import ensure_pipeline_folders, get_pipeline_root

logger = logging.getLogger(__name__)

GROUPING_PREFIX = 'ocdj:'


def get_publish_root():
    """Where publisher drops 06_publish/<id>/ subtrees. Separate from the pipeline
    so the drain daemon has a small bind-mount surface to rsync from.

    VPS: OCDJ_PUBLISH_ROOT=/srv/ocdj/publish (set in compose).
    Mac dev: unset → falls back under the pipeline root for locality.
    """
    return os.environ.get(
        'OCDJ_PUBLISH_ROOT',
        os.path.join(get_pipeline_root(), '06_publish'),
    )


def _write_grouping_tag(filepath, pipeline_item_id):
    """Write `ocdj:<id>` into the file's grouping/GRP1 frame.

    Works for ID3-tagged containers (MP3, AIFF). Mutagen auto-selects. If the
    container doesn't support GRP1 (rare for our AIFF/MP3 outputs), log and
    continue — drain will add without idempotency in that case.
    """
    value = f'{GROUPING_PREFIX}{pipeline_item_id}'
    try:
        try:
            tags = ID3(filepath)
        except mutagen.id3.ID3NoHeaderError:
            tags = ID3()
        tags.delall('GRP1')
        tags.add(GRP1(encoding=3, text=[value]))
        tags.save(filepath)
    except Exception as exc:
        logger.warning(
            f'publisher: could not write grouping tag to {filepath}: {exc}'
        )


def _compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def publish_pipeline_item(item):
    """Transition a PipelineItem from stage=ready to stage=published.

    Steps:
      1. Verify stage == 'ready'.
      2. Write ocdj:<id> into the file's GRP1 grouping tag.
      3. Compute sha256 of the resulting file.
      4. Move to 06_publish/<id>/<filename>. Atomic rename on same FS.
      5. Update PipelineItem fields in one save().

    Idempotent: if archive_state is already publishable/draining/archived,
    returns without changes.
    """
    if item.archive_state in ('publishable', 'draining', 'archived'):
        logger.info(f'publisher: item {item.id} already in archive flow ({item.archive_state}); skip')
        return item

    if item.stage != 'ready':
        raise ValueError(f'publisher: item {item.id} stage={item.stage}, expected ready')

    src = item.current_path
    if not os.path.exists(src):
        raise FileNotFoundError(f'publisher: source missing for item {item.id}: {src}')

    _write_grouping_tag(src, item.id)

    sha = _compute_sha256(src)

    ensure_pipeline_folders()
    publish_root = get_publish_root()
    dest_dir = os.path.join(publish_root, str(item.id))
    os.makedirs(dest_dir, exist_ok=True)
    basename = os.path.basename(src)
    dest = os.path.join(dest_dir, basename)

    shutil.move(src, dest)

    now = timezone.now()
    item.stage = 'published'
    item.archive_state = 'publishable'
    item.sha256 = sha
    item.work_path = dest
    item.published_at = now
    item.save(update_fields=[
        'stage', 'archive_state', 'sha256', 'work_path', 'published_at', 'updated',
    ])
    item.current_path = dest
    item.save(update_fields=['current_path'])
    logger.info(f'publisher: item {item.id} published (sha256={sha[:12]}, dest={dest})')
    return item


def claim_publishable(limit=10, lease_minutes=10):
    """Atomically claim up to `limit` publishable rows for drain.

    Uses SELECT FOR UPDATE SKIP LOCKED in Postgres to prevent two drain
    daemon invocations (or two threads) from claiming the same rows. Also
    reclaims expired `draining` leases.
    """
    from django.db import transaction
    from organize.models import PipelineItem

    now = timezone.now()
    lease_until = now + timedelta(minutes=lease_minutes)

    with transaction.atomic():
        qs = (
            PipelineItem.objects
            .select_for_update(skip_locked=True)
            .filter(archive_state__in=['publishable', 'draining'])
            .filter(
                # publishable rows always claimable; draining only if lease expired
                models_q_publishable_or_expired(now)
            )
            .order_by('published_at', 'id')[:limit]
        )
        claimed_ids = list(qs.values_list('id', flat=True))
        if not claimed_ids:
            return []
        PipelineItem.objects.filter(id__in=claimed_ids).update(
            archive_state='draining',
            draining_until=lease_until,
        )
    return list(PipelineItem.objects.filter(id__in=claimed_ids))


def models_q_publishable_or_expired(now):
    """Q() helper: archive_state=publishable OR (archive_state=draining AND draining_until<now)."""
    from django.db.models import Q
    return Q(archive_state='publishable') | (
        Q(archive_state='draining') & Q(draining_until__lt=now)
    )
