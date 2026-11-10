"""Private command service. Caller must be on the authorized VPS/CI fixture."""
from datetime import datetime
from uuid import UUID

from django.db import transaction

from .models import LibraryTrack, MacLibrarySnapshot
from .snapshots import SOURCE, SnapshotError, digest, replay_status, validate, validate_row


def import_snapshot(payload, *, apply=False):
    validate(payload)  # Entire document before any DB write.
    collection = payload['collection_id']
    prefix = f'mac-snapshot:{collection}:'
    with transaction.atomic():
        if apply:
            MacLibrarySnapshot.objects.get_or_create(collection_id=collection)
            receipt = MacLibrarySnapshot.objects.select_for_update().get(collection_id=collection)
        else:
            receipt = MacLibrarySnapshot.objects.filter(collection_id=collection).first()
        status = replay_status(payload, receipt.captured_at if receipt and receipt.captured_at else None,
                               receipt.digest if receipt else '')
        result = dict(status=status, mode='apply' if apply else 'dry_run', created=0, updated=0, missing=0)
        if status == 'replay':
            return result
        existing = LibraryTrack.objects.filter(source=SOURCE, file_path__startswith=prefix)
        captured_at = datetime.fromisoformat(payload['captured_at'])
        current = set(existing.values_list('file_path', flat=True))
        newer_archives = set(existing.filter(archive_item_id__isnull=False, updated_at__gt=captured_at)
                             .values_list('file_path', flat=True))
        seen = set()
        for row in payload['tracks']:
            identity = prefix + row['id']
            seen.add(identity)
            if identity in newer_archives:
                continue
            result['updated' if identity in current else 'created'] += 1
            if apply:
                defaults = {key: value for key, value in row.items() if key != 'id'}
                defaults.update(source=SOURCE, missing=False, archive_item_id=row.get('archive_item_id'))
                LibraryTrack.objects.update_or_create(file_path=identity, defaults=defaults)
        # A full scan may have started just before a verified drain. Keep
        # its newer registration until a subsequent scan actually covers it.
        omitted = existing.filter(missing=False).exclude(file_path__in=seen).exclude(
            archive_item_id__isnull=False, updated_at__gt=captured_at)
        result['missing'] = omitted.count()
        if apply:
            # Index omission only. Never unlink audio or delete LibraryTrack rows.
            omitted.update(missing=True)
            receipt.captured_at = payload['captured_at']
            receipt.digest = digest(payload)
            receipt.save()
        return result


def register_review_archive(receipt, item):
    """Register one verified Mac file while holding its drain claim lock."""
    try:
        if set(receipt) != {'collection_id', 'track', 'sha256'} or receipt['sha256'] != item.sha256:
            raise ValueError()
        collection = receipt['collection_id']
        if str(UUID(collection)) != collection:
            raise ValueError()
        row = receipt['track']
        validate_row(row)
        if row.get('archive_item_id') != item.pk:
            raise ValueError()
    except (ValueError, AttributeError, KeyError, TypeError):
        raise SnapshotError('Invalid review archive receipt.') from None

    if not MacLibrarySnapshot.objects.select_for_update().filter(
        collection_id=collection,
    ).exclude(captured_at='').first():
        raise SnapshotError('Unknown Mac library collection.')
    identity = f'mac-snapshot:{collection}:{row["id"]}'
    prior = LibraryTrack.objects.select_for_update().filter(file_path=identity).first()
    if prior and prior.archive_item_id != item.pk:
        # A previously moved file can free its name for another download.
        # Retire the old playback ID so existing queues cannot play new bytes.
        prior.file_path = f'retired:{prior.pk}:{identity}'
        prior.missing = True
        prior.save(update_fields=['file_path', 'missing', 'updated_at'])
    defaults = {field: value for field, value in row.items() if field != 'id'}
    LibraryTrack.objects.update_or_create(file_path=identity, defaults={**defaults, 'source': SOURCE, 'missing': False})
