"""Poll slskd for download outcomes (safe to invoke from an operator's cron)."""
from django.core.management.base import BaseCommand

from soulseek.lifecycle import (
    ACTIVE, match_downloads, sync_linked_items, transfer_entries, transfer_status, update_transfer,
)
from soulseek.models import Download
from soulseek.services import SlskdClient


class Command(BaseCommand):
    help = 'Check slskd for completed downloads and update statuses'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show changes without writing')
        parser.add_argument('--limit', type=int, default=0, help='Max active downloads to check (0 = all)')

    def handle(self, *args, **options):
        # All rows are needed to disambiguate legacy attempts, even with --limit.
        downloads = list(Download.objects.all())
        active = [dl for dl in downloads if dl.status in ACTIVE]
        if options['limit']:
            active = active[:options['limit']]
        if not downloads:
            self.stdout.write('No active downloads to check.')
            return
        client = SlskdClient()
        if not client.health():
            self.stderr.write(self.style.ERROR('slskd is not reachable'))
            return
        try:
            entries = transfer_entries(client.get_downloads())
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Failed to fetch transfers: {exc}'))
            return
        matches = match_downloads(downloads, entries)
        completed = failed = 0
        for dl in active:
            if dl.pk not in matches:
                self.stdout.write(f'  {dl.filename} -- not found or ambiguous in slskd transfers')
                continue
            transfer = entries[matches[dl.pk]][1]
            status = transfer_status(transfer)
            completed += status == 'completed'
            failed += status in ('failed', 'cancelled')
            if not options['dry_run']:
                update_transfer(dl, transfer)
            prefix = '[DRY RUN] Would mark' if options['dry_run'] else 'Marked'
            self.stdout.write(f'  {prefix} {status}: {dl.filename}')
        if not options['dry_run']:
            sync_linked_items(entries)
        prefix = '[DRY RUN] ' if options['dry_run'] else ''
        self.stdout.write(f'{prefix}Done: {completed} completed, {failed} failed, '
                          f'{len(active) - completed - failed} still active')
