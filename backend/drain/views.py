"""Drain API — endpoints the Mac drain daemon calls to move files from VPS to iTunes.

State machine: publishable → draining (lease) → archived | (retry) → publishable | failed.

All endpoints are bearer-token authed via require_drain_token. On-VPS-only; Mac dev
environment should never hit these (DRAIN_TOKEN won't be set).
"""

import logging
import os
import shutil

from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status as http_status

from organize.models import PipelineItem
from organize.services.publisher import claim_publishable

from .auth import require_drain_token

logger = logging.getLogger(__name__)

MAX_DRAIN_ATTEMPTS = 5
DEFAULT_LEASE_MINUTES = 10
MAX_BATCH = 25


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
        if not item.work_path or not os.path.exists(item.work_path):
            # Work-path gone before claim landed — mark failed so human can investigate.
            item.archive_state = 'failed'
            item.drain_attempts += 1
            item.error_message = f'work_path missing at claim: {item.work_path}'
            item.save(update_fields=['archive_state', 'drain_attempts', 'error_message', 'updated'])
            logger.error(f'drain: claim found item {item.id} but work_path missing; marked failed')
            continue
        try:
            size = os.path.getsize(item.work_path)
        except OSError:
            size = 0
        payload.append({
            'id': item.id,
            'filename': os.path.basename(item.work_path),
            'work_path': item.work_path,
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

    Body: {"music_persistent_id": "ABC123..."}.
    Transitions draining → archived. Deletes 06_publish/<id>/ tree. Idempotent:
    if already archived, returns the existing row unchanged.
    """
    try:
        item = PipelineItem.objects.get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'not found'}, status=http_status.HTTP_404_NOT_FOUND)

    persistent_id = (request.data.get('music_persistent_id') or '').strip()
    if not persistent_id:
        return Response(
            {'error': 'music_persistent_id required'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

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

    # Delete VPS bytes. Tolerate already-gone (re-entrant confirms after crash).
    publish_dir = os.path.dirname(item.work_path) if item.work_path else ''
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
    item.save(update_fields=[
        'archive_state', 'work_path', 'music_persistent_id',
        'archived_at', 'draining_until', 'updated',
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

    Body: {"reason": "...."}. Lease released so another cycle can retry.
    """
    try:
        item = PipelineItem.objects.get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'not found'}, status=http_status.HTTP_404_NOT_FOUND)

    reason = (request.data.get('reason') or '').strip()[:500]

    if item.archive_state not in ('draining', 'publishable'):
        return Response(
            {
                'error': f'cannot fail from archive_state={item.archive_state}',
                'id': item.id,
            },
            status=http_status.HTTP_409_CONFLICT,
        )

    item.drain_attempts += 1
    item.error_message = (reason or 'drain failed')[:500]
    item.draining_until = None

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
        'archive_state', 'drain_attempts', 'error_message', 'draining_until', 'updated',
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
