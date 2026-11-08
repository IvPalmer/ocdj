import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from uuid import uuid4

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from .models import WantedItem


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL row locks')
class CleanupIntakeTests(TransactionTestCase):
    def setUp(self):
        self.item = WantedItem.objects.create(artist='', title='Night Signal (Lena Remix)')

    def capture(self, event_id, wanted_id=None):
        from .services.cleanup_intake import capture_wanted_cleanup_intent
        return capture_wanted_cleanup_intent(wanted_id or self.item.pk, event_id)

    def test_duplicate_delivery_and_replay_preserve_original_after_edit(self):
        receipt = self.capture(uuid4())
        self.assertEqual(receipt.snapshot['metadata']['artist'], '')
        self.assertEqual(receipt.snapshot['metadata']['title'], self.item.title)
        self.assertEqual(receipt.snapshot['provenance'], {'artist': 'unknown', 'title': 'unknown'})
        self.assertIsNone(receipt.snapshot['metadata_revision'])
        self.assertIsNone(receipt.snapshot['protection_revision'])
        self.assertEqual(receipt.snapshot['granularity'], 'unknown')
        WantedItem.objects.filter(pk=self.item.pk).update(artist='Human correction')
        replay = self.capture(receipt.event_id)
        self.assertEqual(replay.pk, receipt.pk)
        self.assertEqual(replay.snapshot, receipt.snapshot)
        self.item.refresh_from_db()
        self.assertEqual(self.item.artist, 'Human correction')
        self.assertEqual(type(receipt).objects.count(), 1)

    def test_distinct_events_capture_distinct_observations(self):
        first = self.capture(uuid4())
        WantedItem.objects.filter(pk=self.item.pk).update(title='Corrected version')
        second = self.capture(uuid4())
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.snapshot['metadata']['title'], 'Corrected version')

    def test_capture_rolls_back_with_caller(self):
        event = uuid4()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                receipt = self.capture(event)
                raise RuntimeError('synthetic producer rollback')
        self.assertFalse(type(receipt).objects.filter(pk=event).exists())

    def test_reused_key_cannot_retarget_another_subject(self):
        from .services.cleanup_intake import IntakeEventConflict
        receipt = self.capture(uuid4())
        other = WantedItem.objects.create(title='Different recording')
        with self.assertRaises(IntakeEventConflict):
            self.capture(receipt.event_id, other.pk)
        receipt.refresh_from_db()
        self.assertEqual(receipt.snapshot['wanted_id'], self.item.pk)

    def test_receipt_survives_deletion_without_recreating_subject(self):
        receipt = self.capture(uuid4())
        wanted_id = self.item.pk
        self.item.delete()
        replay = self.capture(receipt.event_id, wanted_id)
        self.assertIsNone(replay.wanted_id)
        self.assertEqual(replay.snapshot, receipt.snapshot)
        self.assertFalse(WantedItem.objects.filter(pk=wanted_id).exists())

    def test_missing_subject_creates_no_receipt(self):
        with self.assertRaises(WantedItem.DoesNotExist):
            self.capture(uuid4(), 999999)

    def test_invalid_event_key_rejected_before_database_work(self):
        for value in ('not-a-uuid', '', None, 12):
            with self.subTest(value=value), self.assertNumQueries(0), self.assertRaises(ValueError):
                self.capture(value)

    @staticmethod
    def in_connection(fn):
        close_old_connections()
        try:
            return fn()
        finally:
            close_old_connections()

    def test_concurrent_duplicate_delivery_returns_one_receipt(self):
        barrier = threading.Barrier(2)
        event = uuid4()

        def deliver():
            barrier.wait(timeout=10)
            return self.capture(event).pk

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.in_connection, deliver) for _ in range(2)]
            ids = [future.result(timeout=20) for future in futures]
        self.assertEqual(ids, [event, event])
        from .models import WantedCleanupIntent
        self.assertEqual(WantedCleanupIntent.objects.count(), 1)

    def test_capture_waits_for_concurrent_edit_and_never_overwrites_it(self):
        started = threading.Event()
        completed = threading.Event()

        def capture():
            started.set()
            receipt = self.capture(uuid4())
            completed.set()
            return receipt

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                item = WantedItem.objects.select_for_update().get(pk=self.item.pk)
                future = pool.submit(self.in_connection, capture)
                self.assertTrue(started.wait(timeout=10))
                self.assertFalse(completed.wait(timeout=0.2))
                item.artist = 'Confirmed blank replaced by human'
                item.save(update_fields=['artist'])
            receipt = future.result(timeout=20)
        self.item.refresh_from_db()
        self.assertEqual(self.item.artist, 'Confirmed blank replaced by human')
        self.assertEqual(receipt.snapshot['metadata']['artist'], self.item.artist)
