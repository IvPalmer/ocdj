"""Inactive Wanted receipts: explicit capture and scoped atomic creation."""
from uuid import UUID, uuid4

from django.db import transaction

from wanted.models import WantedCleanupIntent, WantedItem


class IntakeEventConflict(ValueError):
    """The producer reused an intake event key for another Wanted subject."""


def capture_wanted_cleanup_intent(wanted_id, event_id):
    """Capture one observation, or return the original receipt on redelivery.

    The caller must persist/reuse its event UUID across retries. Row locking makes
    the first snapshot coherent with concurrent edits; no Wanted row is written.
    Unknown protection and null revisions prohibit later automatic application.
    This joins an outer producer transaction, if supplied. Capture alone does
    not establish creation coverage or queue dispatch guarantees.
    """
    if not isinstance(event_id, (str, UUID)):
        raise ValueError('event_id must be a UUID')
    event_id = UUID(str(event_id))
    # Normalize IDs so an ordinary serialized PK has the same replay semantics.
    wanted_id = WantedItem._meta.pk.to_python(wanted_id)

    with transaction.atomic():
        receipt = WantedCleanupIntent.objects.filter(event_id=event_id).first()
        if receipt is None:
            item = WantedItem.objects.select_for_update().get(pk=wanted_id)
            receipt, _ = WantedCleanupIntent.objects.get_or_create(
                event_id=event_id,
                defaults={
                    'wanted': item,
                    'snapshot': {
                        'schema_version': 1,
                        'wanted_id': item.pk,
                        'metadata': {field: getattr(item, field) for field in (
                            'artist', 'title', 'release_name', 'catalog_number', 'label',
                        )},
                        'source_id': item.source_id,
                        'granularity': 'unknown',
                        'provenance': {'artist': 'unknown', 'title': 'unknown'},
                        'metadata_revision': None,
                        'protection_revision': None,
                    },
                },
            )
        # Check the retained identity, including after the live FK is cleared.
        # get_or_create's unique event PK also fences concurrent cross-row reuse.
        if receipt.snapshot['wanted_id'] != wanted_id:
            raise IntakeEventConflict('event_id belongs to another Wanted subject')
        return receipt


def create_wanted_with_cleanup_intent(**fields):
    """Insert one Wanted item and its inactive receipt, or roll back both.

    Use only at explicit creation boundaries, never updates. A fresh event key
    belongs to this insertion and is retained by the receipt in the same commit.
    This adds no request idempotency, research dispatch or metadata authority.
    """
    with transaction.atomic():
        item = WantedItem.objects.create(**fields)
        capture_wanted_cleanup_intent(item.pk, uuid4())
        return item
