"""Database-backed API contract tests. Run on the CI PostgreSQL fixture only."""
from django.test import TestCase
from rest_framework.test import APIClient
from .models import LibraryTrack


class LibraryMatchTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def record(self, **overrides):
        fields = dict(artist='Fixture Artist', title='Tone (Dub)',
                      file_path='/synthetic/fixture.flac', format='flac',
                      bitrate=900, sample_rate=44100)
        fields.update(overrides)
        return LibraryTrack.objects.create(**fields)

    def match(self, artist='fixture artist', title='Tone (Dub)'):
        return self.client.get('/api/library/match/', dict(artist=artist, title=title))

    def test_exact_trimmed_case_insensitive_match_returns_only_quality_fields(self):
        track = self.record(artist='  Fixture Artist ', title=' Tone (Dub)  ')
        response = self.match(artist=' FIXTURE ARTIST ', title=' tone (dub) ')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, dict(status='matched', coverage='indexed_only', matches=[
            dict(id=track.id, format='flac', bitrate=900, sample_rate=44100)]))
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_near_matches_and_version_stripping_do_not_match(self):
        self.record()
        for artist, title in [('Fixture Artist', 'Tone'), ('Fixture Artist', 'Tone (Remix)'),
                              ('Fixture Artist feat. B', 'Tone (Dub)'), ('Fixture  Artist', 'Tone (Dub)'),
                              ('Fixture Artist', 'Tone Dub')]:
            with self.subTest(artist=artist, title=title):
                self.assertEqual(self.match(artist, title).data['status'], 'no_indexed_match')

    def test_missing_only_then_available_duplicate(self):
        self.record(missing=True)
        self.assertEqual(self.match().data, dict(status='indexed_missing', coverage='indexed_only', matches=[]))
        track = self.record(file_path='/synthetic/available.flac', sample_rate=None, bitrate=None)
        response = self.match().data
        self.assertEqual(response['status'], 'matched')
        self.assertEqual(response['matches'], [dict(id=track.id, format='flac', bitrate=None, sample_rate=None)])

    def test_multiple_matches_are_bounded_and_do_not_pick_a_winner(self):
        for i in range(5):
            self.record(file_path=f'/synthetic/{i}.flac')
        response = self.match().data
        self.assertEqual(response['status'], 'multiple_matches')
        self.assertEqual(len(response['matches']), 3)

    def test_invalid_input_is_rejected_before_querying(self):
        for params in ({}, {'artist': 'A'}, {'artist': ' ', 'title': 'T'},
                       {'artist': 'A', 'title': 'x' * 501}):
            with self.subTest(params=params), self.assertNumQueries(0):
                self.assertEqual(self.client.get('/api/library/match/', params).status_code, 400)

    def test_empty_index_is_unknown_beyond_index_and_endpoint_is_read_only(self):
        self.assertEqual(self.match().data, dict(status='no_indexed_match', coverage='indexed_only', matches=[]))
        self.assertEqual(self.client.post('/api/library/match/', {}).status_code, 405)
        self.assertEqual(LibraryTrack.objects.count(), 0)
