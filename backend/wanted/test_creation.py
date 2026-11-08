"""Synthetic creation coverage; run only against the disposable CI database."""
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from .models import ImportOperation, WantedCleanupIntent, WantedItem, WantedSource
from .views import WantedItemViewSet, confirm_import
from dig.views import add_item, batch_add


class WantedCreationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def post(self, view, data, **kwargs):
        return view(self.factory.post('/', data, format='json'), **kwargs)

    def assert_receipt(self, item):
        receipt = WantedCleanupIntent.objects.get(wanted=item)
        self.assertEqual(receipt.snapshot['metadata'], {
            'artist': '', 'title': 'Night Signal (Lena Remix)',
            'release_name': 'EP One', 'catalog_number': 'SYN001', 'label': 'Synthetic',
        })
        self.assertEqual(receipt.snapshot['source_id'], item.source_id)
        self.assertEqual(receipt.snapshot['granularity'], 'unknown')
        self.assertEqual(receipt.snapshot['provenance'], {'artist': 'unknown', 'title': 'unknown'})
        self.assertIsNone(receipt.snapshot['metadata_revision'])
        self.assertIsNone(receipt.snapshot['protection_revision'])
        return receipt

    @staticmethod
    def metadata():
        return {'artist': '', 'title': 'Night Signal (Lena Remix)',
                'release_name': 'EP One', 'catalog_number': 'SYN001', 'label': 'Synthetic'}

    def test_rest_create_then_manual_patch_never_recaptures_or_changes_output(self):
        create = WantedItemViewSet.as_view({'post': 'create'})
        response = self.post(create, {**self.metadata(), 'status': 'not_found'})
        self.assertEqual(response.status_code, 201)
        self.assertNotIn('cleanup_intents', response.data)
        item = WantedItem.objects.get(pk=response.data['id'])
        self.assertEqual(item.status, 'not_found')
        receipt = self.assert_receipt(item)
        original = receipt.snapshot
        edit = WantedItemViewSet.as_view({'patch': 'partial_update'})
        response = edit(self.factory.patch('/', {'artist': 'Human edit'}, format='json'), pk=item.pk)
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.artist, 'Human edit')
        self.assertEqual(item.status, 'not_found')
        receipt.refresh_from_db()
        self.assertEqual(receipt.snapshot, original)
        self.assertEqual(WantedCleanupIntent.objects.count(), 1)

    def test_wanted_bulk_creates_distinct_receipts_even_for_identical_items(self):
        view = WantedItemViewSet.as_view({'post': 'bulk_add'})
        response = self.post(view, {'items': [self.metadata(), self.metadata()]})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data), 2)
        for row in response.data:
            self.assert_receipt(WantedItem.objects.get(pk=row['id']))
        self.assertEqual(WantedCleanupIntent.objects.count(), 2)

    def test_confirm_import_captures_actual_insertions_only(self):
        source = WantedSource.objects.create(name='Synthetic', source_type='discogs')
        op = ImportOperation.objects.create(import_type='discogs', status='previewing',
                                            source=source, preview_data=[self.metadata()])
        response = self.post(confirm_import, {'items': [0, 999]}, pk=op.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'completed')
        self.assertEqual(response.data['summary'], {
            'items_imported': 1, 'items_skipped': 1, 'errors': [],
        })
        self.assert_receipt(WantedItem.objects.get())
        self.assertEqual(self.post(confirm_import, {'items': [0]}, pk=op.pk).status_code, 400)
        self.assertEqual(WantedCleanupIntent.objects.count(), 1)

    def test_dig_single_and_batch_duplicates_do_not_recapture(self):
        data = {**self.metadata(), 'source_site': 'discogs'}
        first = self.post(add_item, data)
        self.assertEqual(first.status_code, 201)
        item = WantedItem.objects.get(pk=first.data['item']['id'])
        self.assert_receipt(item)
        duplicate = self.post(add_item, data)
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.data['created'])
        batch = self.post(batch_add, {'source_site': 'discogs', 'items': [self.metadata()]})
        self.assertEqual(batch.status_code, 201)
        self.assertEqual(batch.data['created'], 0)
        self.assertEqual(batch.data['skipped_duplicates'], 1)
        self.assertEqual(WantedCleanupIntent.objects.count(), 1)
        batch = self.post(batch_add, {'source_site': 'discogs', 'skip_duplicates': False,
                                     'items': [self.metadata(), self.metadata()]})
        self.assertEqual(batch.data['created'], 2)
        self.assertEqual(batch.data['skipped_duplicates'], 0)
        for row in batch.data['items']:
            self.assert_receipt(WantedItem.objects.get(pk=row['id']))
        self.assertEqual(WantedCleanupIntent.objects.count(), 3)

    def test_create_and_receipt_roll_back_together_with_outer_transaction(self):
        view = WantedItemViewSet.as_view({'post': 'create'})
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                response = self.post(view, self.metadata())
                self.assertEqual(response.status_code, 201)
                self.assertEqual(WantedCleanupIntent.objects.count(), 1)
                raise RuntimeError('synthetic outer rollback')
        self.assertEqual(WantedItem.objects.count(), 0)
        self.assertEqual(WantedCleanupIntent.objects.count(), 0)

    def test_capture_failure_rolls_back_item_and_receipt_but_import_continues(self):
        from .services.cleanup_intake import capture_wanted_cleanup_intent
        op = ImportOperation.objects.create(import_type='discogs', status='previewing',
                                            preview_data=[self.metadata(), self.metadata()])
        attempts = 0

        def fail_first_after_capture(*args, **kwargs):
            nonlocal attempts
            receipt = capture_wanted_cleanup_intent(*args, **kwargs)
            attempts += 1
            if attempts == 1:
                raise RuntimeError('synthetic receipt failure')
            return receipt

        with patch('wanted.services.cleanup_intake.capture_wanted_cleanup_intent',
                   side_effect=fail_first_after_capture):
            response = self.post(confirm_import, {'items': [0, 1]}, pk=op.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'completed')
        self.assertEqual(response.data['summary'], {
            'items_imported': 1, 'items_skipped': 0, 'errors': ['synthetic receipt failure'],
        })
        self.assertEqual(WantedItem.objects.count(), 1)
        self.assertEqual(WantedCleanupIntent.objects.count(), 1)
        self.assert_receipt(WantedItem.objects.get())
