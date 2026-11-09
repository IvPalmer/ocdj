"""Synthetic review-only research fixtures; no provider calls or database use."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from django.core.management import call_command
from django.test import SimpleTestCase


class CleanupResearchTests(SimpleTestCase):
    def receipt(self, **metadata):
        return {
            'event_id': '00000000-0000-0000-0000-000000000016',
            'snapshot': {
                'schema_version': 1, 'wanted_id': 16,
                'metadata': {'artist': '', 'title': 'Night Signal (Lena Remix)',
                             'release_name': 'EP One', 'catalog_number': 'SYN001',
                             'label': 'Synthetic', **metadata},
                'source_id': None, 'granularity': 'unknown',
                'provenance': {'artist': 'unknown', 'title': 'unknown'},
                'metadata_revision': None, 'protection_revision': None,
            },
        }

    def evidence(self, **fields):
        return {'source_url': 'https://example.invalid/track/16',
                'raw_title': 'Synthetic Artist - Night Signal (Lena Remix)',
                'granularity': 'recording', **fields}

    def propose(self, receipt=None, evidence=None):
        from .services.cleanup_research import build_cleanup_proposal
        return build_cleanup_proposal(
            receipt if receipt is not None else self.receipt(),
            evidence if evidence is not None else [self.evidence()],
        )

    def test_missing_artist_has_inspectable_before_after_and_exact_evidence(self):
        receipt, evidence = self.receipt(), [self.evidence()]
        original = deepcopy((receipt, evidence))
        result = self.propose(receipt, evidence)
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(result['changes'], {
            'artist': {'before': '', 'after': 'Synthetic Artist', 'evidence_indices': [0]},
        })
        self.assertFalse(result['applicable'])
        self.assertIn('recording_identity_unverified', result['application_blockers'])
        self.assertIn('provenance_or_revisions_unknown', result['application_blockers'])
        self.assertEqual(result['receipt'], receipt)
        self.assertEqual(result['evidence'], evidence)
        self.assertEqual(result['candidates'][0]['title'], 'Night Signal (Lena Remix)')
        self.assertEqual((receipt, evidence), original)
        result['receipt']['snapshot']['metadata']['title'] = 'changed output'
        result['evidence'][0]['raw_title'] = 'changed output'
        self.assertEqual((receipt, evidence), original)

    def test_conflicting_candidates_never_choose_first_or_vote(self):
        a, b = self.evidence(), self.evidence(raw_title='Other Artist - Night Signal (Lena Remix)')
        for evidence in ([a, b], [b, a], [a, a, b]):
            result = self.propose(evidence=evidence)
            self.assertEqual(result['status'], 'uncertain')
            self.assertEqual(result['changes'], {})
            self.assertIn('conflicting_candidates', result['reasons'])

    def test_release_unknown_and_wrong_version_are_not_track_evidence(self):
        for fields, reason in [
            ({'granularity': 'release'}, 'source_not_recording'),
            ({'granularity': 'unknown'}, 'source_not_recording'),
            ({'raw_title': 'Synthetic Artist - Night Signal'}, 'title_mismatch'),
            ({'raw_title': 'Synthetic Artist - Night Signal (Other Remix)'}, 'title_mismatch'),
        ]:
            with self.subTest(fields=fields):
                result = self.propose(evidence=[self.evidence(**fields)])
                self.assertEqual(result['changes'], {})
                self.assertIn(reason, result['reasons'])

    def test_lossy_parser_result_is_retained_but_cannot_propose(self):
        result = self.propose(self.receipt(title='Night Signal'), [
            self.evidence(raw_title='Synthetic Artist - Night Signal (Original Mix)'),
        ])
        self.assertEqual(result['candidates'][0]['title'], 'Night Signal')
        self.assertEqual(result['changes'], {})
        self.assertIn('parser_changed_source_text', result['reasons'])
        self.assertIn('(Original Mix)', result['evidence'][0]['raw_title'])

    def test_protected_blank_and_nonempty_human_value_are_preserved(self):
        for provenance in ('manual', 'verified', 'human_confirmed', 'future_unknown_value'):
            receipt = self.receipt()
            receipt['snapshot']['provenance']['artist'] = provenance
            result = self.propose(receipt)
            self.assertEqual(result['changes'], {})
            self.assertIn('field_provenance_blocks_suggestion', result['reasons'])
        result = self.propose(self.receipt(artist='Human correction'))
        self.assertEqual(result['changes'], {})
        self.assertIn('artist_mismatch', result['reasons'])

    def test_incomplete_title_is_candidate_only_without_track_anchor(self):
        for metadata in ({'title': '', 'artist': 'Synthetic Artist'}, {'title': ''}):
            result = self.propose(self.receipt(**metadata))
            self.assertEqual(result['changes'], {})
            self.assertIn('missing_title_anchor', result['reasons'])
            self.assertTrue(result['candidates'])

    def test_empty_unparseable_and_mixed_evidence_remain_uncertain(self):
        for evidence in ([], [self.evidence(raw_title='Night Signal')],
                         [self.evidence(), self.evidence(granularity='release')]):
            result = self.propose(evidence=evidence)
            self.assertEqual(result['status'], 'uncertain')
            self.assertEqual(result['changes'], {})

    def test_repeat_is_deterministic_and_complete_metadata_is_unchanged(self):
        self.assertEqual(self.propose(), self.propose())
        result = self.propose(self.receipt(artist='Synthetic Artist'))
        self.assertEqual(result['status'], 'unchanged')
        self.assertEqual(result['changes'], {})
        self.assertEqual(UUID(result['receipt']['event_id']).int, 22)

    def test_invalid_contracts_fail_closed(self):
        for receipt in ({}, {**self.receipt(), 'event_id': 'bad'},
                        self.receipt(artist=None)):
            with self.subTest(receipt=receipt), self.assertRaises(ValueError):
                self.propose(receipt)
        for evidence in ({}, [None], [self.evidence(source_url='file:///private/data')],
                         [self.evidence(raw_title=42)],
                         [self.evidence(granularity='album')], [self.evidence()] * 21):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.propose(evidence=evidence)

    def test_json_command_emits_proposal_without_mutating_inputs(self):
        with TemporaryDirectory() as directory:
            receipt_path = Path(directory) / 'receipt.json'
            evidence_path = Path(directory) / 'evidence.json'
            receipt_text = json.dumps(self.receipt())
            evidence_text = json.dumps([self.evidence()])
            receipt_path.write_text(receipt_text)
            evidence_path.write_text(evidence_text)
            output = StringIO()
            call_command('research_wanted_cleanup', receipt_json=str(receipt_path),
                         evidence_json=str(evidence_path), stdout=output)
            self.assertEqual(json.loads(output.getvalue()), self.propose())
            self.assertEqual(receipt_path.read_text(), receipt_text)
            self.assertEqual(evidence_path.read_text(), evidence_text)
