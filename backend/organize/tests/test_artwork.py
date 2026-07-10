"""Artwork match-verification tests.

These guard the fix for the wrong-cover bug: a WRONG cover is worse than no
cover, so art is only embedded from a search result whose artist (and, where
applicable, title) actually matches the track. The helpers are pure — no
network — so they run in the hermetic suite.
"""
from django.test import SimpleTestCase

from organize.services.artwork import (
    _artist_ok,
    _credits,
    _is_various,
    _norm_artist,
    _title_ok,
)


class NormArtistTests(SimpleTestCase):
    def test_strips_discogs_disambiguation(self):
        # Discogs disambiguates same-named artists with a trailing " (N)".
        self.assertEqual(_norm_artist('Vsan (2)'), 'vsan')
        self.assertEqual(_norm_artist('Aphex Twin (3)'), 'aphex twin')

    def test_leaves_inner_parens_alone(self):
        # Only a trailing "(N)" is disambiguation; a real (word) stays.
        self.assertEqual(_norm_artist('Underworld (Live)'), 'underworld (live)')

    def test_lowercases_and_trims(self):
        self.assertEqual(_norm_artist('  DJ Koze  '), 'dj koze')

    def test_handles_none(self):
        self.assertEqual(_norm_artist(None), '')

    def test_folds_accents(self):
        # Same artist, accent difference between tag and catalog.
        self.assertEqual(_norm_artist('Âme'), _norm_artist('Ame'))
        self.assertEqual(_norm_artist('Björk'), 'bjork')


class CreditsTests(SimpleTestCase):
    def test_splits_comma_and_ampersand(self):
        self.assertEqual(_credits('Vsan, Guest & Third'), ['vsan', 'guest', 'third'])

    def test_splits_feat(self):
        self.assertEqual(_credits('Artist feat. Someone'), ['artist', 'someone'])

    def test_keeps_names_with_soft_separators_intact(self):
        # Must NOT split on " and " / "/" — they live inside real names.
        self.assertEqual(_credits('Above and Beyond'), ['above and beyond'])
        self.assertEqual(_credits('AC/DC'), ['ac/dc'])

    def test_single_artist(self):
        self.assertEqual(_credits('Vsan'), ['vsan'])


class VariousTests(SimpleTestCase):
    def test_recognizes_various_forms(self):
        self.assertTrue(_is_various('Various'))
        self.assertTrue(_is_various('Various Artists'))
        self.assertTrue(_is_various('VA'))

    def test_real_artist_is_not_various(self):
        self.assertFalse(_is_various('Vsan'))


class ArtistOkTests(SimpleTestCase):
    def test_exact_match(self):
        self.assertTrue(_artist_ok('Vsan', 'Vsan'))

    def test_matches_through_discogs_disambiguation(self):
        # The real-world case: track tag "Vsan" vs Discogs "Vsan (2)".
        self.assertTrue(_artist_ok('Vsan', 'Vsan (2)'))

    def test_rejects_unrelated_artist(self):
        # The anime "Tali Tali" cover that triggered the bug report.
        self.assertFalse(_artist_ok('Vsan', 'Tali Tali'))

    def test_rejects_different_artist(self):
        self.assertFalse(_artist_ok('Vsan', 'Charlotte de Witte'))

    def test_no_want_artist_passes(self):
        # Nothing to check against — don't block.
        self.assertTrue(_artist_ok('', 'Whoever'))

    def test_want_artist_but_result_has_none_fails(self):
        self.assertFalse(_artist_ok('Vsan', ''))

    def test_matches_single_artist_against_multi_credit(self):
        # Track tagged with the primary artist; Spotify lists collaborators.
        self.assertTrue(_artist_ok('Vsan', 'Vsan, Some Guest'))
        self.assertTrue(_artist_ok('Artist', 'Artist feat. Someone'))

    def test_still_rejects_unrelated_within_multi_credit(self):
        self.assertFalse(_artist_ok('Vsan', 'Charlotte de Witte, Amelie Lens'))

    def test_matches_through_accents(self):
        self.assertTrue(_artist_ok('Ame', 'Âme'))


class TitleOkTests(SimpleTestCase):
    def test_exact_title(self):
        self.assertTrue(_title_ok('Talaiot', 'Talaiot'))

    def test_title_within_artist_title_string(self):
        self.assertTrue(_title_ok('Talaiot', 'Vsan - Talaiot'))

    def test_rejects_wrong_title(self):
        self.assertFalse(_title_ok('Talaiot', 'Roxanne'))
        self.assertFalse(_title_ok('Talaiot', 'Walking On The Moon'))

    def test_missing_either_side_passes(self):
        # Can't compare — defer to the artist check rather than block.
        self.assertTrue(_title_ok('', 'Anything'))
        self.assertTrue(_title_ok('Talaiot', ''))
