"""Pure fixture tests; runnable without Django or a database."""
import unittest

from library import matching


class MatchingTests(unittest.TestCase):
    def test_normalization_preserves_versions_punctuation_and_internal_spaces(self):
        self.assertEqual(matching.match_key('  FIXTURE Artist  '), 'fixture artist')
        self.assertNotEqual(matching.match_key('Tone (Dub)'), matching.match_key('Tone'))
        self.assertNotEqual(matching.match_key('A & B'), matching.match_key('A B'))
        self.assertNotEqual(matching.match_key('A  B'), matching.match_key('A B'))

    def test_empty_and_oversized_metadata_are_not_queries(self):
        for value in ('', '   ', '\t', 'x' * 501, None, 123):
            with self.subTest(value=value):
                self.assertEqual(matching.match_key(value), '')

    def test_match_only_exposes_indexed_measurements(self):
        result = matching.match_result([dict(id=1, format='flac', bitrate=None,
            sample_rate=44100, file_path='/synthetic/private.flac', artist='Private fixture')])
        self.assertEqual(result, dict(status='matched', coverage='indexed_only', matches=[
            dict(id=1, format='flac', bitrate=None, sample_rate=44100)]))

    def test_absence_and_missing_are_distinct_and_never_global_absence(self):
        self.assertEqual(matching.match_result([]), dict(
            status='no_indexed_match', coverage='indexed_only', matches=[]))
        self.assertEqual(matching.match_result([], missing=True), dict(
            status='indexed_missing', coverage='indexed_only', matches=[]))

    def test_duplicates_do_not_select_a_quality_winner(self):
        rows = [dict(id=i, format='mp3', bitrate=320, sample_rate=48000) for i in range(4)]
        result = matching.match_result(rows)
        self.assertEqual(result['status'], 'multiple_matches')
        self.assertEqual(len(result['matches']), 3)
        self.assertEqual(result['matches'][0]['id'], 0)

    def test_invalid_measurements_are_unknown_not_quality_claims(self):
        for value in (0, -1, None, '320', True):
            result = matching.match_result([dict(id=1, format='', bitrate=value, sample_rate=value)])
            self.assertEqual(result['matches'][0], dict(id=1, format='', bitrate=None, sample_rate=None))


class RevealMatchingTests(unittest.TestCase):
    collection = '12345678-1234-5678-9abc-123456789abc'
    opaque_id = 'ab' * 32

    def row(self, **overrides):
        row = dict(id=1, format='flac', bitrate=900, sample_rate=44100,
                   source='mac_snapshot', file_path=f'mac-snapshot:{self.collection}:{self.opaque_id}')
        row.update(overrides)
        return row

    def test_single_mac_match_reveals_only_opaque_identity(self):
        result = matching.match_result([self.row()])
        self.assertEqual(result, dict(status='matched', coverage='indexed_only', matches=[
            dict(id=1, format='flac', bitrate=900, sample_rate=44100,
                 reveal_url=f'ocdj-reveal://track/{self.collection}/{self.opaque_id}', playback_id=1)]))

    def test_other_sources_and_legacy_records_have_no_url(self):
        for source in ('manual', 'traxdb', 'organize_pipeline', None, ''):
            with self.subTest(source=source):
                self.assertNotIn('reveal_url', matching.match_result([self.row(source=source)])['matches'][0])

    def test_invalid_or_path_keys_have_no_url(self):
        key = self.row()['file_path']
        for value in (None, '', '/synthetic/private.flac', key.upper(), key + '\n', key + ':extra',
                      key.replace(self.collection, self.collection.replace('-', '')),
                      key.replace(self.collection, 'not-a-uuid'), key[:-1], key + '0',
                      key.replace(self.opaque_id, 'g' * 64),
                      key.replace(self.opaque_id, '../private.flac')):
            with self.subTest(key=value):
                result = matching.match_result([self.row(file_path=value)])
                self.assertNotIn('reveal_url', result['matches'][0])
                self.assertNotIn('file_path', result['matches'][0])

    def test_ambiguous_and_unavailable_matches_have_no_url(self):
        for rows in ([self.row(), self.row(id=2)], [self.row(), self.row(id=2, source='manual')]):
            result = matching.match_result(rows)
            self.assertEqual(result['status'], 'multiple_matches')
            self.assertTrue(all('reveal_url' not in match for match in result['matches']))
        self.assertEqual(matching.match_result([], missing=True)['matches'], [])
        self.assertEqual(matching.match_result([])['matches'], [])
