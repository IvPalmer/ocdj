"""Private command service. Caller must be on the authorized VPS/CI fixture."""
from django.db import transaction

from .models import LibraryTrack, MacLibrarySnapshot
from .snapshots import SOURCE, digest, replay_status, validate


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
        current = set(existing.values_list('file_path', flat=True))
        seen = set()
        for row in payload['tracks']:
            identity = prefix + row['id']
            seen.add(identity)
            result['updated' if identity in current else 'created'] += 1
            if apply:
                defaults = {key: value for key, value in row.items() if key != 'id'}
                defaults.update(source=SOURCE, missing=False)
                LibraryTrack.objects.update_or_create(file_path=identity, defaults=defaults)
        omitted = existing.filter(missing=False).exclude(file_path__in=seen)
        result['missing'] = omitted.count()
        if apply:
            # Index omission only. Never unlink audio or delete LibraryTrack rows.
            omitted.update(missing=True)
            receipt.captured_at = payload['captured_at']
            receipt.digest = digest(payload)
            receipt.save()
        return result
