"""Render a private review artifact from supplied JSON, without accessing models."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from wanted.services.cleanup_research import build_cleanup_proposal


class Command(BaseCommand):
    help = 'Dry-run Wanted cleanup research using a saved receipt and source title evidence'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--receipt-json', required=True, help='Private receipt envelope JSON file')
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument('--evidence-json', help='Private source observation list JSON file')
        source.add_argument('--catalog', choices=['itunes'], help='Explicitly query one catalog once')

    def handle(self, *args, **options):
        try:
            receipt = json.loads(Path(options['receipt_json']).read_text(encoding='utf-8'))
            if options.get('catalog') == 'itunes':
                from wanted.services.cleanup_catalog import research_cleanup_catalog
                proposal = research_cleanup_catalog(receipt)
            else:
                evidence = json.loads(Path(options['evidence_json']).read_text(encoding='utf-8'))
                proposal = build_cleanup_proposal(receipt, evidence)
        except (OSError, ValueError) as exc:
            # Do not echo input contents or private paths into logs on failure.
            raise CommandError('Cannot build proposal: invalid or unreadable input JSON') from exc
        self.stdout.write(json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True))
