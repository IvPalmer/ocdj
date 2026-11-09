"""Synthetic iTunes responses only; SimpleTestCase prohibits database queries."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from . import test_cleanup_research as receipt_fixtures


class CleanupCatalogTests(SimpleTestCase):
    def receipt(self, **metadata):
        return receipt_fixtures.CleanupResearchTests().receipt(**metadata)

    def row(self, **fields):
        return {'wrapperType': 'track', 'kind': 'song', 'trackId': 161,
                'artistName': 'Synthetic Artist', 'trackName': 'Night Signal (Lena Remix)',
                'trackViewUrl': 'https://music.apple.com/us/song/synthetic/161', **fields}

    def lookup(self, rows=None, receipt=None, payload=None, error=None):
        from .services.cleanup_catalog import research_cleanup_catalog
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        response = self.session.get.return_value.__enter__.return_value
        response.status_code = 200
        if payload is None:
            rows = [self.row()] if rows is None else rows
            payload = {'resultCount': len(rows), 'results': rows}
        response.iter_content.return_value = [json.dumps(payload).encode()]
        self.session.get.side_effect = error
        with patch('wanted.services.preview.requests.Session', return_value=self.session):
            return research_cleanup_catalog(receipt if receipt is not None else self.receipt())

    def test_missing_artist_search_retains_evidence_without_claiming_identity(self):
        receipt = self.receipt()
        before = deepcopy(receipt)
        result = self.lookup(receipt=receipt)
        self.assertEqual(result['lookup']['status'], 'results')
        self.assertEqual(result['lookup']['query'], 'Night Signal (Lena Remix)')
        self.assertEqual(result['evidence'][0]['provider_payload'], self.row())
        self.assertEqual(result['evidence'][0]['provider_id'], 161)
        self.assertEqual(result['candidates'][0]['artist'], 'Synthetic Artist')
        self.assertEqual(result['changes'], {})
        self.assertFalse(result['applicable'])
        self.assertIn('source_not_recording', result['reasons'])
        self.assertEqual(receipt, before)
        self.session.get.assert_called_once_with(
            'https://itunes.apple.com/search',
            params={'term': 'Night Signal (Lena Remix)', 'entity': 'song', 'limit': 3},
            timeout=12, allow_redirects=False, stream=True)
        self.assertFalse(self.session.trust_env)

    def test_same_title_multiple_ids_artists_versions_are_all_reviewable(self):
        for rows in ([self.row(), self.row(trackId=162)],
                     [self.row(), self.row(trackId=162, artistName='Other Artist')],
                     [self.row(), self.row(trackId=162, trackName='Night Signal (Original Mix)')]):
            with self.subTest(rows=rows):
                result = self.lookup(rows=rows)
                self.assertEqual(len(result['evidence']), 2)
                self.assertEqual(result['changes'], {})
                self.assertFalse(result['applicable'])
        result = self.lookup(rows=[self.row(trackName='Night Signal (Original Mix)')],
                             receipt=self.receipt(title='Night Signal'))
        self.assertIn('parser_changed_source_text', result['reasons'])
        self.assertIn('Original Mix', result['evidence'][0]['provider_payload']['trackName'])

    def test_empty_transport_and_malformed_responses_are_distinct(self):
        self.assertEqual(self.lookup(rows=[])['lookup']['status'], 'no_result')
        for error in (requests.Timeout('private url'), requests.ConnectionError('private url')):
            result = self.lookup(error=error)
            self.assertEqual(result['lookup']['status'], 'transport_failure')
            self.assertNotIn('private url', json.dumps(result))
            self.assertEqual(result['changes'], {})
            self.session.get.assert_called_once()
        for payload in ({}, {'resultCount': 0, 'results': [self.row()]}, [],
                        {'resultCount': 1, 'results': [self.row(trackId=True)]},
                        {'resultCount': 1, 'results': [self.row(trackViewUrl='https://evil.invalid/161')]},
                        {'resultCount': 1, 'results': [self.row(kind='music-video')]},
                        {'resultCount': 4, 'results': [self.row()] * 4}):
            with self.subTest(payload=payload):
                result = self.lookup(payload=payload)
                self.assertEqual(result['lookup']['status'], 'invalid_response')
                self.assertEqual(result['evidence'], [])

    def test_invalid_receipt_or_missing_anchor_never_calls_provider(self):
        for receipt in (self.receipt(title=''), self.receipt(title='x' * 513)):
            result = self.lookup(receipt=receipt)
            self.assertEqual(result['lookup']['status'], 'not_queried')
            self.session.get.assert_not_called()
        with self.assertRaises(ValueError):
            self.lookup(receipt={})
        self.session.get.assert_not_called()

    def test_protected_fields_and_release_subject_stay_unchanged(self):
        for receipt in (self.receipt(artist='Human correction'), self.receipt()):
            receipt['snapshot']['provenance']['artist'] = 'manual'
            receipt['snapshot']['granularity'] = 'release'
            result = self.lookup(receipt=receipt)
            self.assertEqual(result['changes'], {})
            self.assertIn('subject_is_release', result['reasons'])
            self.assertEqual(result['receipt'], receipt)

    def test_catalog_command_is_explicit_and_keeps_offline_mode(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt.json'
            path.write_text(json.dumps(self.receipt()))
            result = self.lookup()
            output = StringIO()
            with patch('wanted.services.cleanup_catalog.research_cleanup_catalog', return_value=result) as lookup:
                call_command('research_wanted_cleanup', receipt_json=str(path), catalog='itunes', stdout=output)
            self.assertEqual(json.loads(output.getvalue()), result)
            lookup.assert_called_once_with(self.receipt())
            with self.assertRaises(CommandError):
                call_command('research_wanted_cleanup', receipt_json=str(path), stdout=StringIO())

    def test_http_redirect_bad_json_and_oversized_body_fail_without_retry(self):
        from .services.preview import search_itunes_catalog
        for status, chunks in ((302, [b'{}']), (503, [b'{}']),
                               (200, [b'not json']), (200, [b'x' * 131073])):
            with self.subTest(status=status, size=len(chunks[0])):
                session = MagicMock()
                session.__enter__.return_value = session
                response = session.get.return_value.__enter__.return_value
                response.status_code = status
                response.iter_content.return_value = chunks
                with patch('wanted.services.preview.requests.Session', return_value=session):
                    with self.assertRaises((ValueError, requests.RequestException)):
                        search_itunes_catalog('', 'Synthetic')
                session.get.assert_called_once()
