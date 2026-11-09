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
        parser.add_argument('--evidence-json', required=True, help='Private source observation list JSON file')

    def handle(self, *args, **options):
        try:
            receipt = json.loads(Path(options['receipt_json']).read_text(encoding='utf-8'))
            evidence = json.loads(Path(options['evidence_json']).read_text(encoding='utf-8'))
            proposal = build_cleanup_proposal(receipt, evidence)
        except (OSError, ValueError) as exc:
            # Do not echo input contents or private paths into logs on failure.
            raise CommandError('Cannot build proposal: invalid or unreadable input JSON') from exc
        self.stdout.write(json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True))
