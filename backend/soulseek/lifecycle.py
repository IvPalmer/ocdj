"""Transfer identity and linked state; history visibility never erases evidence."""
from collections import defaultdict

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from wanted.models import WantedItem
from .models import Download, SearchQueueItem, SearchResult

ACTIVE = ('queued', 'downloading')
TERMINAL = ('completed', 'failed', 'cancelled')


def is_terminal_transfer(state):
    flags = {flag.strip() for flag in (state or '').split(',')}
    return 'Completed' in flags and not flags.intersection({'InProgress', 'Queued'})


def transfer_status(transfer):
    state = transfer.get('state') or ''
    if is_terminal_transfer(state):
        if 'Succeeded' in state:
            return 'completed'
        return 'cancelled' if 'Cancelled' in state else 'failed'
    return 'downloading' if 'InProgress' in state else 'queued'


def transfer_entries(users):
    return [(user.get('username', ''), transfer)
            for user in users for directory in user.get('directories', [])
            for transfer in directory.get('files', [])]


def file_key(username, filename):
    return username.casefold(), filename.casefold()


def match_downloads(downloads, entries):
    """Return row ID -> snapshot index; filename fallback must be unambiguous.

    An explicit transfer ID must never fall back to another attempt of that file.
    Include hidden provenance when testing ambiguity, and never let a hidden
    terminal record claim a live transfer, even if slskd reuses its old ID.
    """
    by_id, by_file, legacy_by_file = defaultdict(list), defaultdict(list), defaultdict(list)
    for index, (peer, transfer) in enumerate(entries):
        by_id[(peer.casefold(), transfer.get('id'))].append(index)
        by_file[file_key(peer, transfer.get('filename') or '')].append(index)
    known_ids = {(dl.username.casefold(), dl.slskd_id) for dl in downloads if dl.slskd_id}
    for dl in downloads:
        if not dl.slskd_id:
            legacy_by_file[file_key(dl.username, dl.filename)].append(dl.pk)
    matches = {}
    for dl in downloads:
        key = file_key(dl.username, dl.filename)
        if dl.slskd_id:
            candidates = by_id.get((dl.username.casefold(), dl.slskd_id), [])
        elif dl.hidden_at is not None and dl.status in TERMINAL:
            # With no recorded ID a dismissed legacy row cannot distinguish
            # old remote history from a later terminal retry. Leave it untracked.
            continue
        elif len(legacy_by_file[key]) == 1:
            candidates = [index for index in by_file.get(key, [])
                          if (entries[index][0].casefold(), entries[index][1].get('id')) not in known_ids]
        else:
            candidates = []
        if len(candidates) != 1:
            continue
        index = candidates[0]
        peer, transfer = entries[index]
        if file_key(peer, transfer.get('filename') or '') != key:
            continue
        if dl.status in TERMINAL and not is_terminal_transfer(transfer.get('state')):
            continue
        matches[dl.pk] = index
    return matches


def update_transfer(dl, transfer):
    """Reconcile an active attempt only; return True for a new completion.

    Terminal outcomes are immutable here. A retry is a separate Download, not a
    reason to resurrect a dismissed success or failure.
    """
    # Bind a uniquely resolved legacy identity before it can be hidden, so
    # stale remote history cannot later be confused with another attempt.
    if not dl.slskd_id and transfer.get('id') and (dl.hidden_at is None or dl.status in ACTIVE):
        if Download.objects.filter(pk=dl.pk, slskd_id='').update(slskd_id=transfer['id']):
            dl.slskd_id = transfer['id']
    if dl.status not in ACTIVE:
        return False
    status = transfer_status(transfer)
    fields = {'status': status, 'progress': transfer.get('percentComplete', dl.progress)}
    if status == 'completed':
        fields.update(progress=100, completed_at=timezone.now())
    elif status in ('failed', 'cancelled'):
        fields['error_message'] = transfer.get('state') or ''
    changed = Download.objects.filter(pk=dl.pk, status__in=ACTIVE).update(**fields)
    if changed:
        for field, value in fields.items():
            setattr(dl, field, value)
    return bool(changed and status == 'completed')


def hide_downloads(ids):
    """Hide terminal history once, retaining identity, outcome and every FK."""
    return Download.objects.filter(pk__in=ids, status__in=TERMINAL, hidden_at__isnull=True).update(
        hidden_at=timezone.now())


def _parent_status(attempts, live_files, result_files):
    if not attempts or any(dl.status in ACTIVE for dl in attempts):
        return None
    known_files = {file_key(dl.username, dl.filename) for dl in attempts} | result_files
    if live_files.intersection(known_files):
        return None
    if any(dl.status == 'completed' for dl in attempts):
        return 'downloaded'
    if all(dl.status in ('failed', 'cancelled') for dl in attempts):
        return 'found'
    return None


def sync_linked_items(entries):
    """Reconcile only downloading parents with known attempts and live evidence.

    No-row legacy parents are intentionally unknown, not failed. The guarded
    one-off procedure in HISTORY.md handles the operator-confirmed orphan.
    Query all provenance, including hidden rows and attempts outside the UI cap.
    Only update the status/updated fields, preserving human metadata and later
    pipeline states such as tagged/organized.
    """
    live_files = {file_key(peer, transfer.get('filename') or '')
                  for peer, transfer in entries if not is_terminal_transfer(transfer.get('state'))}
    with transaction.atomic():
        # Same lock order as enqueue: queue parents before Wanted parents.
        for qi in SearchQueueItem.objects.select_for_update().filter(status='downloading').order_by('pk'):
            attempts = list(Download.objects.filter(queue_item=qi))
            result_files = {file_key(peer, filename) for peer, filename in
                            SearchResult.objects.filter(queue_item=qi).values_list('username', 'filename')}
            status = _parent_status(attempts, live_files, result_files)
            if status:
                SearchQueueItem.objects.filter(pk=qi.pk, status='downloading').update(status=status, updated=timezone.now())
        for wi in WantedItem.objects.select_for_update().filter(status='downloading').order_by('pk'):
            attempts = list(Download.objects.filter(Q(wanted_item=wi) | Q(queue_item__wanted_item=wi)))
            # Another queue with no durable attempts may represent an unknown
            # legacy transfer. Never reset its Wanted item based on this queue.
            if wi.queue_items.filter(status='downloading', downloads__isnull=True).exists():
                continue
            result_files = {file_key(peer, filename) for peer, filename in
                            SearchResult.objects.filter(Q(wanted_item=wi) | Q(queue_item__wanted_item=wi))
                            .values_list('username', 'filename')}
            status = _parent_status(attempts, live_files, result_files)
            if status:
                WantedItem.objects.filter(pk=wi.pk, status='downloading').update(status=status, updated=timezone.now())
