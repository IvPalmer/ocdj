"""
Find pending WantedItems with metadata and create SearchQueueItems for them.

Designed to run via cron to auto-queue new wanted items for Soulseek search.
"""
import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

from wanted.models import WantedItem
from soulseek.models import SearchQueueItem

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Queue pending WantedItems for Soulseek search'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would happen without making changes',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Max number of items to process (0 = all)',
        )
        parser.add_argument(
            '--status',
            default='pending',
            help='WantedItem status to process (default: pending)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        target_status = options['status']

        # Find wanted items that have enough metadata to search
        items = WantedItem.objects.filter(
            status=target_status,
        ).filter(
            # Must have at least artist+title, or a catalog number
            Q(artist__gt='', title__gt='') | Q(catalog_number__gt='')
        )

        if limit:
            items = items[:limit]

        total = items.count()
        if total == 0:
            self.stdout.write(f'No {target_status} wanted items with searchable metadata.')
            return

        self.stdout.write(f'Processing {total} {target_status} wanted item(s)...')

        created = 0
        skipped = 0

        for item in items:
            # Skip if a queue item already exists for this wanted item
            existing = SearchQueueItem.objects.filter(
                wanted_item=item,
                status__in=['pending', 'searching', 'found', 'downloading'],
            ).exists()

            if existing:
                self.stdout.write(f'  Skipped (already queued): {item}')
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f'  [DRY RUN] Would create queue item: {item}')
            else:
                SearchQueueItem.objects.create(
                    wanted_item=item,
                    artist=item.artist,
                    title=item.title,
                    release_name=item.release_name,
                    catalog_number=item.catalog_number,
                    label=item.label,
                    status='pending',
                )
                item.status = 'searching'
                item.save()
                self.stdout.write(self.style.SUCCESS(f'  Queued: {item}'))

            created += 1

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(
            f'\n{prefix}Done: {created} queued, {skipped} skipped (already queued)'
        )
