"""Private file import only: no HTTP upload, credentials, jobs, or media IO."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from library.snapshots import SnapshotError, load_snapshot
from library.snapshot_import import import_snapshot


class Command(BaseCommand):
    help = 'Preview a private Mac snapshot; --apply commits atomically. VPS/CI only.'

    def add_arguments(self, parser):
        parser.add_argument('snapshot')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            payload = load_snapshot(options['snapshot'])
            result = import_snapshot(payload, apply=options['apply'])
        except (SnapshotError, DatabaseError):
            # Database exceptions may include complete rows/tags. Suppress details.
            raise CommandError('Import refused or rolled back; verify snapshot and database schema.') from None
        self.stdout.write(json.dumps(result, sort_keys=True))
