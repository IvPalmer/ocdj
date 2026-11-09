"""Explicit bounded review intake; default preview performs no writes/HTTP."""
import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from wanted.models import MetadataCleanup
from wanted.services.legacy_review import MAX_BATCH, PREFIX, capture_legacy, legacy_rows


class Command(BaseCommand):
    help = 'Preview, enqueue or summarize protected historical Wanted review (never applies edits).'

    def add_arguments(self, parser):
        parser.add_argument('--through-id', type=int, required=True,
                            help='Frozen historical upper PK, established privately by operator.')
        parser.add_argument('--after-id', type=int, default=0)
        parser.add_argument('--limit', type=int, default=25)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--dry-run', action='store_true', help='Default; no dispatch or provider calls.')
        mode.add_argument('--enqueue', action='store_true', help='Persist review receipts and dispatch asynchronously.')
        mode.add_argument('--report', action='store_true', help='Summarize existing receipts, without private tags.')

    def handle(self, *args, **options):
        upper, after, limit = options['through_id'], options['after_id'], options['limit']
        if upper <= 0 or after < 0 or after >= upper or not 1 <= limit <= MAX_BATCH:
            raise CommandError('Require 0 <= after-id < through-id and 1 <= limit <= 100.')
        ids = list(legacy_rows(through_id=upper, after_id=after).values_list('pk', flat=True)[:limit])
        receipts = MetadataCleanup.objects.filter(subject_key__in=[f'{PREFIX}{pk}' for pk in ids])
        summary = {'mode': 'report' if options['report'] else 'enqueue' if options['enqueue'] else 'dry-run',
                   'selected': len(ids), 'through_id': upper, 'next_after_id': ids[-1] if ids else after,
                   'existing': receipts.count(), 'created': 0, 'skipped': 0}
        if options['enqueue']:
            for pk in ids:
                receipt, created = capture_legacy(pk, through_id=upper)
                summary['created'] += int(created)
                summary['skipped'] += int(receipt is None)
        if options['report']:
            statuses, assessments = Counter(), Counter()
            candidates = hypotheses = 0
            for receipt in receipts:
                statuses[receipt.status] += 1
                candidates += len(receipt.evidence.get('candidates', []))
                hypotheses += bool(receipt.result.get('proposed_changes'))
                assessments.update(row['assessment'] for row in receipt.result.get('candidate_assessments', []))
            summary.update(statuses=dict(statuses), candidates=candidates,
                           anchored_hypotheses=hypotheses, assessments=dict(assessments))
        self.stdout.write(json.dumps(summary, sort_keys=True))
