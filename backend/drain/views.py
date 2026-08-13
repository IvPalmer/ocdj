"""Drain API — endpoints the Mac drain daemon calls to move files from VPS to iTunes.

State machine: publishable → draining (lease) → archived | (retry) → publishable | failed.

All endpoints are bearer-token authed via require_drain_token. On-VPS-only; Mac dev
environment should never hit these (DRAIN_TOKEN won't be set).
"""

import hmac
import logging
import os
import shutil

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status as http_status

from organize.models import PipelineItem
from organize.services.publisher import claim_publishable, is_canonical_publish_dir

from .auth import require_drain_token

logger = logging.getLogger(__name__)

MAX_DRAIN_ATTEMPTS = 5
DEFAULT_LEASE_MINUTES = 10
MAX_BATCH = 25


def _check_claim_token(request, item):
    """Return an error Response when the caller isn't holding the live claim.

    The lease was never enough on its own. A daemon can confirm after its lease
    expired, and in between the operator may have edited the track — which
    rewrites the bytes and clears the token. Honouring that confirmation would
    delete the *new* file and archive the row: silent loss of the only copy.
    So confirm/fail must echo the token minted by the claim they are reporting
    on, and any token that has since been rotated or cleared is refused.
    """
    presented = (request.data.get('claim_token') or '').strip()
    if not item.claim_token:
        return Response(
            {
                'error': 'no active claim for this item; re-claim it via /publishable/',
                'id': item.id,
            },
            status=http_status.HTTP_409_CONFLICT,
        )
    if not presented or not hmac.compare_digest(presented, item.claim_token):
        logger.warning(
            f'drain: stale or missing claim token for item {item.id} — refusing'
        )
        return Response(
            {
                'error': 'stale claim token; the artifact changed since you claimed it',
                'id': item.id,
            },
            status=http_status.HTTP_409_CONFLICT,
        )
    return None


@api_view(['GET'])
@require_drain_token
def drain_publishable(request):
    """Atomically claim up to `limit` publishable/expired-draining items.

    Returns rows with fields the Mac daemon needs to fetch + verify.
    Claimed rows flip to archive_state=draining with a 10-min lease.
    """
    try:
        limit = int(request.query_params.get('limit', '10'))
    except ValueError:
        limit = 10
    limit = max(1, min(limit, MAX_BATCH))

    items = claim_publishable(limit=limit, lease_minutes=DEFAULT_LEASE_MINUTES)
    payload = []
    for item in items:
        # Both checks happen here, not at confirm time: a row the daemon can't
        # legally finish (no file, or a file outside its own publish dir, which
        # confirm will refuse to delete) would otherwise be re-claimed forever
        # and could never be repaired, because 'draining' rejects edits.
        problem = ''
        if not item.work_path or not os.path.isfile(item.work_path):
            problem = f'work_path missing at claim: {item.work_path}'
        elif not is_canonical_publish_dir(item.id, os.path.dirname(item.work_path)):
            problem = f'work_path outside the publish directory: {item.work_path}'
        if problem:
            item.archive_state = 'failed'
            item.drain_attempts += 1
            item.error_message = problem
            item.claim_token = ''
            item.save(update_fields=[
                'archive_state', 'drain_attempts', 'error_message',
                'claim_token', 'updated',
            ])
            logger.error(f'drain: item {item.id} unclaimable — {problem}; marked failed')
            continue
        try:
            size = os.path.getsize(item.work_path)
        except OSError:
            size = 0
        payload.append({
            'id': item.id,
            'filename': os.path.basename(item.work_path),
            'work_path': item.work_path,
            'claim_token': item.claim_token,
            'sha256': item.sha256,
            'size': size,
            'drain_attempts': item.drain_attempts,
            'artist': item.artist,
            'title': item.title,
        })
    return Response({'items': payload, 'count': len(payload)})


@api_view(['POST'])
@require_drain_token
def drain_confirm(request, pk):
    """Daemon confirms the track was added to Music.app.

    Body: {"music_persistent_id": "ABC123...", "claim_token": "<from claim>"}.
    Transitions draining → archived. Deletes 06_publish/<id>/ tree. Idempotent:
    if already archived, returns the existing row unchanged.
    """
    persistent_id = (request.data.get('music_persistent_id') or '').strip()
    if not persistent_id:
        return Response(
            {'error': 'music_persistent_id required'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    # Row lock for the whole check-then-delete: validating the token and then
    # deleting a directory in two steps leaves a window where a re-claim (or a
    # refresh) rotates the artifact between the check and the rmtree.
    with transaction.atomic():
        return _confirm_locked(request, pk, persistent_id)


def _confirm_locked(request, pk, persistent_id):
    try:
        item = PipelineItem.objects.select_for_update().get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'not found'}, status=http_status.HTTP_404_NOT_FOUND)

    if item.archive_state == 'archived':
        return Response({
            'id': item.id,
            'archive_state': item.archive_state,
            'music_persistent_id': item.music_persistent_id,
            'idempotent': True,
        })

    if item.archive_state not in ('draining', 'publishable'):
        return Response(
            {
                'error': f'cannot confirm from archive_state={item.archive_state}',
                'id': item.id,
            },
            status=http_status.HTTP_409_CONFLICT,
        )

    stale = _check_claim_token(request, item)
    if stale is not None:
        return stale

    # Delete VPS bytes. Tolerate already-gone (re-entrant confirms after crash).
    publish_dir = os.path.dirname(item.work_path) if item.work_path else ''
    # rmtree takes a whole directory, so it only ever runs against the one
    # directory this item is allowed to own. A work_path corrupted by a bug or
    # a manual edit must not be able to aim it somewhere else.
    if publish_dir and not is_canonical_publish_dir(item.id, publish_dir):
        logger.error(
            f'drain: refusing to delete non-canonical dir for item {item.id}: {publish_dir}'
        )
        return Response(
            {
                'error': 'work_path is outside this item\'s publish directory; refusing to delete',
                'id': item.id,
                'work_path': item.work_path,
            },
            status=http_status.HTTP_409_CONFLICT,
        )
    if publish_dir and os.path.isdir(publish_dir):
        try:
            shutil.rmtree(publish_dir)
        except OSError as exc:
            logger.error(f'drain: could not rm {publish_dir}: {exc}')
            return Response(
                {'error': f'rmtree failed: {exc}'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    item.archive_state = 'archived'
    item.work_path = ''
    item.music_persistent_id = persistent_id
    item.archived_at = timezone.now()
    item.draining_until = None
    item.claim_token = ''
    item.save(update_fields=[
        'archive_state', 'work_path', 'music_persistent_id',
        'archived_at', 'draining_until', 'claim_token', 'updated',
    ])

    logger.info(f'drain: item {item.id} archived (persistent_id={persistent_id})')

    return Response({
        'id': item.id,
        'archive_state': item.archive_state,
        'music_persistent_id': item.music_persistent_id,
    })


@api_view(['POST'])
@require_drain_token
def drain_fail(request, pk):
    """Daemon reports a drain failure. Increments attempts; at MAX → permanent fail.

    Body: {"reason": "....", "claim_token": "<from claim>"}. Lease released so
    another cycle can retry.
    """
    reason = (request.data.get('reason') or '').strip()[:500]

    with transaction.atomic():
        return _fail_locked(request, pk, reason)


def _fail_locked(request, pk, reason):
    try:
        item = PipelineItem.objects.select_for_update().get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'not found'}, status=http_status.HTTP_404_NOT_FOUND)

    if item.archive_state not in ('draining', 'publishable'):
        return Response(
            {
                'error': f'cannot fail from archive_state={item.archive_state}',
                'id': item.id,
            },
            status=http_status.HTTP_409_CONFLICT,
        )

    stale = _check_claim_token(request, item)
    if stale is not None:
        return stale

    item.drain_attempts += 1
    item.error_message = (reason or 'drain failed')[:500]
    item.draining_until = None
    # The claim is over either way; the next cycle mints a fresh token.
    item.claim_token = ''

    if item.drain_attempts >= MAX_DRAIN_ATTEMPTS:
        item.archive_state = 'failed'
        logger.error(
            f'drain: item {item.id} exceeded {MAX_DRAIN_ATTEMPTS} attempts; '
            f'archive_state=failed (last reason: {reason})'
        )
    else:
        # Back to the pool for a retry next cycle.
        item.archive_state = 'publishable'

    item.save(update_fields=[
        'archive_state', 'drain_attempts', 'error_message', 'draining_until',
        'claim_token', 'updated',
    ])

    return Response({
        'id': item.id,
        'archive_state': item.archive_state,
        'drain_attempts': item.drain_attempts,
    })


@api_view(['GET'])
@require_drain_token
def drain_health(request):
    """Cheap endpoint so the daemon's preflight can verify token + reachability."""
    return Response({'ok': True, 'ts': timezone.now().isoformat()})
