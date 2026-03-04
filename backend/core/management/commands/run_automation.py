"""
Run one automation cycle: Wanted -> Search -> Download -> Organize.

Designed for cron use. Reads config from DB (AUTOMATION_* keys).
"""
import json
import logging

from django.core.management.base import BaseCommand

from core.services.automation import run_automation_cycle, get_automation_config

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run one end-to-end automation cycle (wanted -> search -> download -> organize)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would happen without making changes',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Print detailed output for each step',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        verbose = options['verbose']

        config = get_automation_config()
        if not config['AUTOMATION_ENABLED']:
            self.stdout.write(self.style.WARNING(
                'Automation is disabled. Enable via AUTOMATION_ENABLED config.'
            ))
            if not dry_run:
                return

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(f'{prefix}Running automation cycle...')

        if verbose:
            self.stdout.write(f'  Config: {json.dumps(config, indent=2)}')

        report = run_automation_cycle(dry_run=dry_run)

        # Print results
        for step_name, step_data in report['steps'].items():
            if step_data.get('skipped'):
                self.stdout.write(f'  {step_name}: skipped ({step_data.get("reason", "")})')
                continue

            if step_name == 'search':
                self.stdout.write(self.style.SUCCESS(
                    f'  search: {step_data["queued"]} queued, '
                    f'{step_data["already_queued"]} already queued'
                ))

            elif step_name == 'download':
                self.stdout.write(self.style.SUCCESS(
                    f'  download: {step_data["downloaded"]} started, '
                    f'{step_data["below_threshold"]} below threshold ({step_data["threshold"]}%)'
                ))

            elif step_name == 'organize':
                self.stdout.write(self.style.SUCCESS(
                    f'  organize: {step_data["ingested"]} ingested, '
                    f'{step_data["failed"]} failed'
                ))

            if verbose and step_data.get('items'):
                for item in step_data['items']:
                    self.stdout.write(f'    - {item.get("label", item.get("file", ""))}')

        self.stdout.write(f'{prefix}Done.')
