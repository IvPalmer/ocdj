"""
Poll slskd for completed downloads and update DB status.

Designed to run via cron (e.g. every 5 minutes).
"""
import logging
from datetime import timezone, datetime

from django.core.management.base import BaseCommand

from soulseek.models import Download
from soulseek.services import SlskdClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check slskd for completed downloads and update statuses'

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
            help='Max number of downloads to check (0 = all)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']

        # Find downloads we're tracking that aren't terminal
        active_downloads = Download.objects.filter(
            status__in=['queued', 'downloading'],
        ).select_related('queue_item', 'wanted_item')

        if limit:
            active_downloads = active_downloads[:limit]

        count = active_downloads.count()
        if count == 0:
            self.stdout.write('No active downloads to check.')
            return

        self.stdout.write(f'Checking {count} active download(s)...')

        client = SlskdClient()

        # Health check
        health = client.health()
        if not health:
            self.stderr.write(self.style.ERROR('slskd is not reachable'))
            return

        # Get all transfers from slskd
        try:
            all_transfers = client.get_downloads()
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Failed to fetch transfers: {e}'))
            return

        # Build lookup: username -> filename -> transfer state
        transfer_map = {}
        for user_entry in all_transfers:
            username = user_entry.get('username', '')
            for directory in user_entry.get('directories', []):
                for file_transfer in directory.get('files', []):
                    fname = file_transfer.get('filename', '')
                    key = (username, fname)
                    transfer_map[key] = file_transfer

        completed = 0
        failed = 0

        for dl in active_downloads:
            transfer = transfer_map.get((dl.username, dl.filename))

            if not transfer:
                self.stdout.write(f'  {dl.filename} -- not found in slskd transfers')
                continue

            state = transfer.get('state', '')
            # slskd states: Queued, Initializing, InProgress, Completed, Errored, Cancelled, etc.
            # State can be compound like "Completed, Succeeded"

            if 'Completed' in state and 'Succeeded' in state:
                if dry_run:
                    self.stdout.write(f'  [DRY RUN] Would mark completed: {dl.filename}')
                else:
                    dl.status = 'completed'
                    dl.progress = 100
                    dl.completed_at = datetime.now(tz=timezone.utc)
                    dl.save()

                    # Update linked queue item
                    if dl.queue_item and dl.queue_item.status == 'downloading':
                        dl.queue_item.status = 'downloaded'
                        dl.queue_item.save()

                    # Update linked wanted item
                    if dl.wanted_item and dl.wanted_item.status == 'downloading':
                        dl.wanted_item.status = 'downloaded'
                        dl.wanted_item.save()

                    self.stdout.write(self.style.SUCCESS(f'  Completed: {dl.filename}'))
                completed += 1

            elif 'Completed' in state and ('Cancelled' in state or 'Errored' in state):
                if dry_run:
                    self.stdout.write(f'  [DRY RUN] Would mark failed: {dl.filename}')
                else:
                    dl.status = 'failed'
                    dl.error_message = state
                    dl.save()
                    self.stdout.write(self.style.WARNING(f'  Failed: {dl.filename} ({state})'))
                failed += 1

            else:
                # Still in progress -- update progress percentage
                bytes_transferred = transfer.get('bytesTransferred', 0)
                size = transfer.get('size', 0)
                if size > 0:
                    progress = (bytes_transferred / size) * 100
                    if not dry_run and dl.status != 'downloading':
                        dl.status = 'downloading'
                        dl.progress = progress
                        dl.save()
                    self.stdout.write(f'  In progress: {dl.filename} ({progress:.0f}%)')

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(
            f'\n{prefix}Done: {completed} completed, {failed} failed, '
            f'{count - completed - failed} still active'
        )
