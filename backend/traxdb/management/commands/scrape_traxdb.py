"""
Trigger a TraxDB blog scrape (sync operation).

Designed to run via cron (e.g. daily). Runs synchronously -- the sync
happens in the same process, not a background thread.
"""
import logging

from django.core.management.base import BaseCommand

from traxdb.models import TraxDBOperation
from traxdb.services import run_sync

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Scrape TraxDB blog for new download links'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would happen without running the sync',
        )
        parser.add_argument(
            '--max-pages',
            type=int,
            default=50,
            help='Maximum blog pages to scrape (default: 50)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        max_pages = options['max_pages']

        # Check for already-running sync
        if TraxDBOperation.objects.filter(op_type='sync', status='running').exists():
            self.stderr.write(self.style.ERROR('A sync is already running. Aborting.'))
            return

        if dry_run:
            self.stdout.write(
                f'[DRY RUN] Would scrape up to {max_pages} pages from TraxDB blog'
            )
            latest = TraxDBOperation.objects.filter(
                op_type='sync', status='completed',
            ).first()
            if latest:
                self.stdout.write(f'  Last successful sync: {latest.created}')
                if latest.summary:
                    self.stdout.write(
                        f'  Found {latest.summary.get("links_found_count", "?")} links, '
                        f'{latest.summary.get("links_new_count", "?")} new'
                    )
            else:
                self.stdout.write('  No previous sync found.')
            return

        self.stdout.write(f'Starting TraxDB sync (max {max_pages} pages)...')

        op = TraxDBOperation.objects.create(op_type='sync')

        # Run synchronously (not in a thread) for cron use
        run_sync(op.id, max_pages=max_pages)

        # Reload to get final state
        op.refresh_from_db()

        if op.status == 'completed':
            summary = op.summary or {}
            self.stdout.write(self.style.SUCCESS(
                f'Sync completed: {summary.get("links_found_count", 0)} links found, '
                f'{summary.get("links_new_count", 0)} new'
            ))
        else:
            self.stderr.write(self.style.ERROR(
                f'Sync failed: {op.error_message}'
            ))
