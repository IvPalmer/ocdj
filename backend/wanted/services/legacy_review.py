"""Explicit historical cohort selection; no Wanted metadata writes."""
from django.db import transaction
from django.db.models import CharField, Exists, OuterRef, Value
from django.db.models.functions import Cast, Concat

from wanted.models import MetadataCleanup, WantedItem
from .cleanup import _capture
from .cleanup_evidence import clean_isrc

PREFIX = 'wanted-legacy-review:v1:'
MAX_BATCH = 100


def legacy_rows(*, through_id, after_id=0):
    # Since 0010, ordinary creates capture wanted:<pk> atomically. Protection
    # alone is insufficient: new manually confirmed items are protected too.
    # An explicit frozen upper bound also excludes later uncaptured raw inserts.
    # Match ordinary keys exactly, without trusting source labels.
    ordinary = MetadataCleanup.objects.filter(
        subject_key=Concat(Value('wanted:'), Cast(OuterRef('pk'), CharField())))
    return WantedItem.objects.filter(
        pk__gt=after_id, pk__lte=through_id, metadata_protected=True,
    ).annotate(has_capture=Exists(ordinary)).filter(has_capture=False).order_by('pk')


def capture_legacy(item_id, *, through_id):
    with transaction.atomic():
        item = legacy_rows(through_id=through_id).select_for_update().filter(pk=item_id).first()
        if item is None:
            return None, False
        key = f'{PREFIX}{item.pk}'
        existing = MetadataCleanup.objects.filter(subject_key=key).first()
        if existing:
            return existing, False
        snapshot = {'artist': item.artist, 'title': item.title,
                    'isrc': clean_isrc(item.cleanup_isrc), 'protected': True,
                    'revision': item.metadata_revision, 'review_only': True,
                    'cohort': 'legacy_without_capture_v1'}
        # Only writes a receipt, and dispatches through the same Huey lock and
        # bounded outbox recovery as ordinary cleanup. Never saves the item.
        return _capture(key, snapshot, wanted_id=item.pk), True
