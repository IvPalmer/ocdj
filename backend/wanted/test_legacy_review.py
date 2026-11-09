"""Synthetic historical cohort and race proof, PostgreSQL CI only."""
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import json
import threading
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory

from wanted.models import MetadataCleanup, WantedItem
from wanted.services.cleanup import process_cleanup
from wanted.services.legacy_review import PREFIX, capture_legacy
from wanted.test_cleanup_evidence import CODE, TITLE, evidence


def legacy(**changes):
    # Emulate rows protected by 0010, without the later ordinary-create hook.
    return WantedItem.objects.bulk_create([WantedItem(
        artist='Fixture Label', title=TITLE, metadata_protected=True, **changes)])[0]


class LegacyReviewTests(TestCase):
    def setUp(self):
        self.dispatch = patch('wanted.services.cleanup.dispatch').start()
        self.collect = patch('wanted.services.cleanup.collect_evidence', return_value=evidence()).start()
        self.addCleanup(patch.stopall)

    def command(self, **options):
        output = StringIO()
        call_command('review_legacy_wanted', stdout=output, **options)
        return json.loads(output.getvalue())

    def test_preview_default_and_explicit_dry_run_never_write_or_query(self):
        item = legacy()
        for extra in ({}, {'dry_run': True}):
            result = self.command(through_id=item.pk, **extra)
            self.assertEqual(result['selected'], 1)
            self.assertEqual(result['mode'], 'dry-run')
        self.assertFalse(MetadataCleanup.objects.exists())
        self.dispatch.assert_not_called()
        self.collect.assert_not_called()

    def test_cohort_bounds_exclude_new_captures_even_if_protected(self):
        first = legacy()
        new = WantedItem.objects.create(artist='Human', title=TITLE, metadata_protected=True)
        last = legacy()
        result = self.command(through_id=last.pk, after_id=first.pk, enqueue=True)
        self.assertEqual(result['selected'], 1)
        self.assertEqual(result['next_after_id'], last.pk)
        self.assertFalse(MetadataCleanup.objects.filter(subject_key=f'{PREFIX}{new.pk}').exists())
        self.assertEqual(self.command(through_id=last.pk, limit=1)['next_after_id'], first.pk)
        self.collect.assert_not_called()

    def test_invalid_bounds_fail_closed(self):
        for options in ({'through_id': 0}, {'through_id': 10, 'after_id': 10},
                        {'through_id': 10, 'after_id': -1}, {'through_id': 10, 'limit': 101},
                        {'through_id': 10, 'limit': 0}):
            with self.assertRaises(CommandError):
                self.command(**options)
        self.assertFalse(MetadataCleanup.objects.exists())

    def test_enqueue_replay_and_edit_preserve_first_snapshot_and_dispatch_once(self):
        item = legacy()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.command(through_id=item.pk, enqueue=True)['created'], 1)
        receipt = MetadataCleanup.objects.get()
        before = receipt.snapshot
        item.artist = 'Owner correction'
        item.save(update_fields=['artist'])
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.command(through_id=item.pk, enqueue=True)['created'], 0)
        receipt.refresh_from_db()
        self.assertEqual(receipt.snapshot, before)
        self.dispatch.assert_called_once_with(receipt.pk)

    def test_anchored_hypothesis_never_writes_any_wanted_field(self):
        item = legacy(cleanup_isrc=CODE)
        before = WantedItem.objects.filter(pk=item.pk).values().get()
        receipt, _ = capture_legacy(item.pk, through_id=item.pk)
        process_cleanup(receipt.pk)
        process_cleanup(receipt.pk)
        receipt.refresh_from_db()
        self.assertEqual(WantedItem.objects.filter(pk=item.pk).values().get(), before)
        self.assertEqual(receipt.status, 'proposed')
        self.assertEqual(receipt.result['changes'], {})
        self.assertEqual(receipt.result['proposed_changes'], {'artist': 'Fixture Artist'})
        self.assertEqual(receipt.attempts, 1)
        self.assertEqual(receipt.evidence, evidence())

    def test_review_search_uses_full_title_not_bad_import_credit(self):
        item = legacy()
        receipt, _ = capture_legacy(item.pk, through_id=item.pk)
        process_cleanup(receipt.pk)
        self.assertEqual(self.collect.call_args.args[0]['artist'], '')
        self.assertEqual(self.collect.call_args.args[0]['title'], TITLE)
        receipt.refresh_from_db()
        self.assertEqual(receipt.snapshot['artist'], 'Fixture Label')

    def test_key_fence_survives_unprotection_and_missing_review_flag(self):
        item = legacy(cleanup_isrc=CODE)
        receipt, _ = capture_legacy(item.pk, through_id=item.pk)
        # Defense in depth: the key alone keeps this off the application path.
        snapshot = {**receipt.snapshot, 'protected': False}
        snapshot.pop('review_only')
        MetadataCleanup.objects.filter(pk=receipt.pk).update(snapshot=snapshot)
        WantedItem.objects.filter(pk=item.pk).update(metadata_protected=False)
        process_cleanup(receipt.pk)
        item.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(item.artist, 'Fixture Label')
        self.assertEqual(receipt.status, 'proposed')
        self.assertEqual(receipt.result['changes'], {})

    def test_deleted_or_edited_subject_is_superseded_with_evidence_retained(self):
        for delete in (False, True):
            item = legacy(cleanup_isrc=CODE)
            receipt, _ = capture_legacy(item.pk, through_id=item.pk)
            if delete:
                item.delete()
            else:
                item.artist = 'Owner correction'
                item.save(update_fields=['artist'])
            process_cleanup(receipt.pk)
            receipt.refresh_from_db()
            self.assertEqual(receipt.status, 'superseded')
            self.assertEqual(receipt.evidence, evidence())
            if not delete:
                item.refresh_from_db()
                self.assertEqual(item.artist, 'Owner correction')

    def test_report_is_aggregate_only_and_api_exposes_private_receipt(self):
        from wanted.views import WantedItemViewSet
        item = legacy(cleanup_isrc=CODE)
        receipt, _ = capture_legacy(item.pk, through_id=item.pk)
        process_cleanup(receipt.pk)
        report = self.command(through_id=item.pk, report=True)
        self.assertEqual(report['statuses'], {'proposed': 1})
        self.assertEqual(report['anchored_hypotheses'], 1)
        self.assertNotIn('Fixture', json.dumps(report))
        response = WantedItemViewSet.as_view({'get': 'cleanup'})(APIRequestFactory().get('/'), pk=item.pk)
        self.assertEqual(response.data['subject_key'], receipt.subject_key)
        self.assertEqual(response.data['snapshot']['artist'], 'Fixture Label')


class LegacyReviewRaceTests(TransactionTestCase):
    def setUp(self):
        patch('wanted.services.cleanup.dispatch').start()
        self.addCleanup(patch.stopall)

    def test_concurrent_capture_has_one_receipt(self):
        item = legacy()
        barrier = threading.Barrier(2)
        def capture():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return capture_legacy(item.pk, through_id=item.pk)[0].pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(capture) for _ in range(2)]
            self.assertEqual(futures[0].result(timeout=15), futures[1].result(timeout=15))
        self.assertEqual(MetadataCleanup.objects.count(), 1)

    def test_real_edit_commits_while_lookup_runs_and_is_never_overwritten(self):
        item = legacy(cleanup_isrc=CODE)
        receipt, _ = capture_legacy(item.pk, through_id=item.pk)
        started, release = threading.Event(), threading.Event()
        def lookup(snapshot):
            started.set()
            if not release.wait(timeout=10):
                raise AssertionError('edit did not complete')
            return evidence()
        def process():
            close_old_connections()
            try:
                process_cleanup(receipt.pk)
            finally:
                close_old_connections()
        with patch('wanted.services.cleanup.collect_evidence', side_effect=lookup):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(process)
                try:
                    self.assertTrue(started.wait(timeout=10))
                    item.artist = 'Concurrent owner correction'
                    item.save(update_fields=['artist'])
                finally:
                    release.set()
                future.result(timeout=15)
        item.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(item.artist, 'Concurrent owner correction')
        self.assertTrue(item.metadata_protected)
        self.assertEqual(receipt.status, 'superseded')
