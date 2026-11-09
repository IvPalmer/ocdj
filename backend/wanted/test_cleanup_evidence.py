"""Pure synthetic fixtures; also runnable without Django setup or a database."""
import copy
import unittest
from unittest.mock import MagicMock, patch

import requests
from wanted.services.cleanup_evidence import clean_isrc, collect_evidence, evaluate, evaluate_review, _get_json

CODE = 'USAAA2400001'
RECORDING = '510e4d03-487b-4748-86a1-63d850557c25'
TITLE = 'Night Signal (Lena Remix)'


def snapshot(**changes):
    return {'artist': '', 'title': TITLE, 'isrc': CODE, 'protected': False, 'revision': 0, **changes}


def evidence(**changes):
    candidate = {'provider': 'musicbrainz', 'id': RECORDING, 'artist': 'Fixture Artist',
                 'title': TITLE, 'isrc': CODE, 'isrcs': [CODE], 'disambiguation': '', **changes}
    return {'kind': 'isrc_lookup', 'isrc': CODE, 'candidates': [candidate]}


class CleanupPolicyTests(unittest.TestCase):
    def test_clear_missing_artist_only(self):
        self.assertEqual(evaluate(snapshot(), evidence())['changes'], {'artist': 'Fixture Artist'})

    def test_version_variants_never_collapse(self):
        for title in ['Night Signal', 'Night Signal (Original Mix)', 'Night Signal (Lena Edit)',
                      'Night Signal (Lena Remix) (Remastered)', 'Night Signal - Lena Remix']:
            with self.subTest(title=title):
                self.assertEqual(evaluate(snapshot(), evidence(title=title))['changes'], {})

    def test_isrc_collision_is_ambiguous_even_if_names_agree(self):
        e = evidence()
        e['candidates'].append(copy.deepcopy(e['candidates'][0]))
        self.assertEqual(evaluate(snapshot(), e)['changes'], {})

    def test_search_never_authorizes(self):
        e = evidence()
        e['kind'] = 'title_search'
        self.assertEqual(evaluate(snapshot(), e)['changes'], {})

    def test_disambiguation_or_unconfirmed_isrc_blocks(self):
        for changes in [{'disambiguation': 'radio edit'}, {'isrcs': []}, {'isrc': 'USAAA2400002'}]:
            self.assertEqual(evaluate(snapshot(), evidence(**changes))['changes'], {})

    def test_exact_isrc_repairs_artist_and_strips_confirmed_title_prefix(self):
        self.assertEqual(evaluate(snapshot(artist='Wrong tag'), evidence())['changes'],
                         {'artist': 'Fixture Artist'})
        self.assertEqual(evaluate(snapshot(artist='Wrong tag', title=f'Fixture Artist - {TITLE}'),
                                  evidence())['changes'], {'artist': 'Fixture Artist', 'title': TITLE})
        self.assertEqual(evaluate(snapshot(title='Incomplete'), evidence())['changes'], {})

    def test_catalog_search_corroborates_explicit_artist_credit_only(self):
        candidate = {'provider': 'itunes', 'artist': 'Fixture Artist', 'title': TITLE}
        search = {'kind': 'title_search', 'candidates': [candidate]}
        explicit = snapshot(isrc='', title=f'Fixture Artist - {TITLE}')
        self.assertEqual(evaluate(explicit, search)['changes'], {'artist': 'Fixture Artist', 'title': TITLE})
        self.assertEqual(evaluate(snapshot(isrc=''), search)['changes'], {})
        self.assertEqual(evaluate({**explicit, 'artist': 'Uploader'}, search)['changes'], {})
        self.assertEqual(evaluate({**explicit, 'protected': True}, search)['changes'], {})
        self.assertEqual(evaluate(explicit, {**search, 'candidates': [candidate, candidate]})['changes'], {})

    def test_placeholder_artist_is_not_a_recovered_credit(self):
        for artist in ['Various Artists', '[unknown]']:
            self.assertEqual(evaluate(snapshot(), evidence(artist=artist))['changes'], {})

    def test_protected_blank_is_preserved(self):
        self.assertEqual(evaluate(snapshot(protected=True), evidence())['reason'], 'protected')

    def test_no_anchor_no_title_no_evidence(self):
        self.assertEqual(evaluate(snapshot(isrc=''), evidence())['changes'], {})
        self.assertEqual(evaluate(snapshot(title=''), evidence())['changes'], {})
        self.assertEqual(evaluate(snapshot(), {'candidates': []})['reason'], 'no_evidence')

    def test_full_title_case_whitespace_and_unicode_only(self):
        self.assertEqual(evaluate(snapshot(title='  NIGHT  SIGNAL (LENA REMIX) '), evidence())['changes'],
                         {'artist': 'Fixture Artist'})

    def test_deterministic_and_no_input_mutation(self):
        s, e = snapshot(), evidence()
        before = copy.deepcopy((s, e))
        self.assertEqual(evaluate(s, e), evaluate(s, e))
        self.assertEqual((s, e), before)

    def test_isrc_validation(self):
        self.assertEqual(clean_isrc('us-aaa-24-00001'), CODE)
        for value in [None, 123, '/../../secret', 'x', CODE + 'a']:
            self.assertEqual(clean_isrc(value), '')


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch('wanted.services.cleanup_evidence.time.sleep').start()
        self.addCleanup(patch.stopall)

    def test_isrc_lookup_retains_track_credit_and_full_version(self):
        payload = {'isrc': CODE, 'recordings': [{'id': RECORDING, 'title': TITLE,
                   'artist-credit': [{'name': 'Fixture', 'joinphrase': ' & '}, {'name': 'Artist'}],
                   'isrcs': [CODE]}]}
        with patch('wanted.services.cleanup_evidence._get_json', return_value=payload) as get, \
             patch('wanted.services.cleanup_evidence.time.sleep'):
            result = collect_evidence(snapshot())
        self.assertEqual(result['candidates'][0]['artist'], 'Fixture & Artist')
        self.assertEqual(result['candidates'][0]['title'], TITLE)
        self.assertEqual(get.call_count, 1)
        self.assertNotIn('Night', get.call_args.args[0])

    def test_search_is_bounded_and_proposal_only(self):
        payload = {'resultCount': 1, 'results': [{'wrapperType': 'track', 'kind': 'song',
                  'trackId': 123, 'artistName': 'Fixture Artist', 'trackName': TITLE}]}
        with patch('wanted.services.cleanup_evidence._get_json', return_value=payload) as get:
            result = collect_evidence(snapshot(isrc=''))
        self.assertEqual(get.call_args.args[1]['limit'], 3)
        self.assertEqual(evaluate(snapshot(isrc=''), result)['changes'], {})
        self.sleep.assert_called_once_with(3.1)

    def test_search_failure_also_observes_catalog_cooldown(self):
        with patch('wanted.services.cleanup_evidence._get_json', side_effect=requests.Timeout):
            with self.assertRaises(requests.Timeout):
                collect_evidence(snapshot(isrc=''))
        self.sleep.assert_called_once_with(3.1)

    def test_invalid_provider_shapes_are_rejected(self):
        for payload in [[], {}, {'isrc': 'USAAA2400002', 'recordings': []},
                        {'isrc': CODE, 'recordings': 'not a list'},
                        {'isrc': CODE, 'recordings': [{'id': 'not-a-uuid'}]}]:
            with patch('wanted.services.cleanup_evidence._get_json', return_value=payload), \
                 patch('wanted.services.cleanup_evidence.time.sleep'):
                with self.assertRaises((ValueError, KeyError)):
                    collect_evidence(snapshot())

    def test_no_title_skips_search(self):
        with patch('wanted.services.cleanup_evidence._get_json') as get:
            self.assertEqual(collect_evidence(snapshot(isrc='', title=''))['kind'], 'not_queried')
        get.assert_not_called()

    def test_malformed_identity_fields_cannot_authorize(self):
        for changes in [{'isrcs': CODE}, {'isrcs': [None]}, {'disambiguation': {}},
                        {'disambiguation': []}]:
            row = {'id': RECORDING, 'title': TITLE, 'artist-credit': [{'name': 'Fixture Artist'}],
                   'isrcs': [CODE], **changes}
            with patch('wanted.services.cleanup_evidence._get_json', return_value={'isrc': CODE, 'recordings': [row]}), \
                 patch('wanted.services.cleanup_evidence.time.sleep'):
                with self.assertRaises(ValueError):
                    collect_evidence(snapshot())

    def _session(self, status=200, chunks=None):
        response = MagicMock(status_code=status)
        response.iter_content.return_value = chunks or [b'{"results": []}']
        session = MagicMock()
        session.get.return_value.__enter__.return_value = response
        session.__enter__.return_value = session
        return session

    def test_transport_disables_environment_auth_redirects_and_bounds_reads(self):
        session = self._session()
        with patch('wanted.services.cleanup_evidence.requests.Session', return_value=session):
            _get_json('https://example.test', {})
        self.assertFalse(session.trust_env)
        opts = session.get.call_args.kwargs
        self.assertFalse(opts['allow_redirects'])
        self.assertTrue(opts['stream'])
        self.assertEqual(opts['timeout'], (5, 12))
        self.assertIn('OCDJ', opts['headers']['User-Agent'])

    def test_oversized_response_and_non_200_fail_closed(self):
        for session, exception in [(self._session(chunks=[b'x' * 131073]), ValueError),
                                   (self._session(status=302), requests.HTTPError),
                                   (self._session(status=429), requests.HTTPError)]:
            with patch('wanted.services.cleanup_evidence.requests.Session', return_value=session):
                with self.assertRaises(exception):
                    _get_json('https://example.test', {})

    def test_not_found_and_broken_json(self):
        with patch('wanted.services.cleanup_evidence.requests.Session', return_value=self._session(status=404)):
            self.assertEqual(_get_json('https://example.test', {}, missing={'recordings': []}), {'recordings': []})
        with patch('wanted.services.cleanup_evidence.requests.Session', return_value=self._session(chunks=[b'{'])):
            with self.assertRaises(ValueError):
                _get_json('https://example.test', {})


class LegacyReviewPolicyTests(unittest.TestCase):
    def test_protected_anchored_hypothesis_is_not_a_write(self):
        original = snapshot(artist='Fixture Label', protected=True)
        before = copy.deepcopy(original)
        result = evaluate_review(original, evidence())
        self.assertEqual(result['changes'], {})
        self.assertEqual(result['proposed_changes'], {'artist': 'Fixture Artist'})
        self.assertEqual(original, before)

    def test_search_full_title_is_unverified_and_remix_conflict_has_no_delta(self):
        original = snapshot(artist='Compilation', protected=True, isrc='')
        for title, assessment in [(TITLE, 'full_title_only_identity_unverified'),
                                  ('Night Signal (Other Remix)', 'version_or_title_conflict')]:
            e = {'kind': 'title_search', 'candidates': [{'provider': 'itunes', 'id': 1,
                 'artist': 'Fixture Artist', 'title': title}]}
            result = evaluate_review(original, e)
            self.assertEqual(result['proposed_changes'], {})
            self.assertEqual(result['candidate_assessments'][0]['assessment'], assessment)
        result = evaluate_review(snapshot(protected=True), evidence(title='Night Signal'))
        self.assertEqual(result['proposed_changes'], {})
        self.assertEqual(result['candidate_assessments'][0]['assessment'], 'version_or_title_conflict')
