"""The one operation that may change a published artifact.

Editing a published track used to be two client calls — PATCH the metadata,
then POST retag — with the file rewritten in place in between. That sequence
broke two tracks: retag renamed the file without moving `work_path`, and it
changed the bytes without recomputing `sha256`, so the drain daemon failed
first with "work_path missing at claim" and would then have failed with
"sha256 mismatch". A claim landing between the two calls made it worse: the
DB metadata had already changed while the Mac was downloading the old bytes.

So published artifacts get exactly one mutating operation, and it does the
whole job under a row lock: tag → rename → re-hash → re-publish. The publisher
is deliberately not reused; it models the workbench→publish transition and
skips anything already in the archive flow.
"""

import logging
import os
import shutil

from django.db import transaction

from organize.models import PipelineItem

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = (
    'artist', 'title', 'album', 'label',
    'catalog_number', 'genre', 'year', 'track_number',
)

# States a published artifact can be refreshed from. 'draining' is excluded on
# purpose: the Mac is holding those bytes right now. 'archived' has no bytes.
REFRESHABLE_STATES = ('publishable', 'failed')


class RefreshError(Exception):
    """A refusal the API layer turns into a 409."""


def resolve_artifact_path(item):
    """The published file as it actually exists on disk, or None.

    `work_path` is authoritative but can go stale (the incident); falling back
    to `current_path` is what makes a broken row repairable instead of a 404.
    """
    for path in (item.work_path, item.current_path):
        if path and os.path.exists(path):
            return path
    return None


def _ensure_in_publish_dir(item, path):
    """Published bytes live in <publish>/<id>/ — the daemon rsyncs that dir."""
    from .publisher import canonical_publish_dir, is_canonical_publish_dir

    if is_canonical_publish_dir(item.id, os.path.dirname(path)):
        return path
    dest_dir = canonical_publish_dir(item.id)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    shutil.move(path, dest)
    logger.info(f'refresh: item {item.id} relocated into publish dir ({dest})')
    return dest


def refresh_published_artifact(item_id, metadata=None):
    """Apply metadata to a published artifact and re-publish it consistently.

    Under `select_for_update`: write tags to a temp copy and swap it in, rename
    to match the new metadata, recompute sha256, then persist current_path /
    work_path / final_filename / sha256 / archive_state and clear the failure
    bookkeeping in a single save. Any outstanding claim token is dropped, so a
    drain confirmation for the pre-edit bytes can no longer be honoured.

    Raises RefreshError for anything the caller should see as a 409.
    """
    from .publisher import compute_sha256
    from .renamer import rename_file
    from .tagger import _clean_genre, _clean_metadata, write_tags_atomic

    with transaction.atomic():
        try:
            item = PipelineItem.objects.select_for_update().get(pk=item_id)
        except PipelineItem.DoesNotExist:
            raise RefreshError('not found')

        if item.stage != 'published':
            raise RefreshError(
                f'item {item.id} is on the workbench (stage={item.stage}); '
                'refresh is for published artifacts'
            )
        if item.archive_state not in REFRESHABLE_STATES:
            raise RefreshError(
                f'cannot refresh while archive_state={item.archive_state}'
            )

        # Normalise once, then use the same values for the row, the embedded
        # tags and the filename. write_tags and rename_file each clean their
        # own input, so assigning the raw form values to the row would leave
        # the table describing a track differently from its tags and its name.
        incoming = {field: getattr(item, field) for field in EDITABLE_FIELDS}
        for field in EDITABLE_FIELDS:
            if metadata and field in metadata:
                value = metadata[field] or ''
                if field == 'genre':
                    value = _clean_genre(value)
                incoming[field] = value
        for field, value in _clean_metadata(incoming).items():
            setattr(item, field, value)

        path = resolve_artifact_path(item)
        if path is None:
            raise RefreshError(
                f'no file on disk for item {item.id} '
                f'(work_path={item.work_path!r}, current_path={item.current_path!r})'
            )

        path = _ensure_in_publish_dir(item, path)
        item.current_path = path
        item.work_path = path

        tags = {field: getattr(item, field) for field in EDITABLE_FIELDS}
        write_tags_atomic(path, tags)
        rename_file(item, commit=False)

        item.sha256 = compute_sha256(item.work_path)
        item.archive_state = 'publishable'
        item.claim_token = ''
        item.error_message = ''
        item.drain_attempts = 0
        item.draining_until = None
        item.metadata_source = 'manual' if metadata else item.metadata_source
        item.save(update_fields=[
            *EDITABLE_FIELDS,
            'current_path', 'work_path', 'final_filename', 'sha256',
            'archive_state', 'claim_token', 'error_message', 'drain_attempts',
            'draining_until', 'metadata_source', 'updated',
        ])

    logger.info(
        f'refresh: item {item.id} re-published '
        f'(sha256={item.sha256[:12]}, work_path={item.work_path})'
    )
    return item
