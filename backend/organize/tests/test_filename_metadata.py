"""Turning a dropped file's name into tags and a filename (#98).

Evidence from the 2026-11-12 _incoming batch: "Sisto-Turn It Up (DDC011)"
came back as artist "Sisto (2)" and title "Sisto-Turn It Up (DDC011)"; a
parenthesised catalog with no metadata match stayed in the title; a bracketed
catalog was stripped from the title and lost.

The end-to-end cases write real WAV files (stdlib `wave`): write_tags sends
WAV through the same ID3 writer as the AIFF the Mac receives.
"""

import os
import tempfile
import wave
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from mutagen.easyid3 import EasyID3
from mutagen.wave import WAVE

from organize.models import PipelineItem
from organize.services.renamer import (
    _strip_artist_prefix,
    clean_artist,
    clean_title,
    rename_file,
    split_catalog_number,
)
from organize.services.tagger import _clean_metadata, tag_file

CATALOG_NUMBERS = [
    'DDC011', 'GRAVITATE04', 'SILKYDIGI05', 'PT301', 'CAT635662', 'BSU014',
    'ALQ005', 'HAWS033', 'BRANE003', 'GRDT-001', 'CAT 635662',
]
NOT_CATALOG_NUMBERS = [
    'Original Mix', "Swag's Alternative Dub", 'Osunlade Remix', 'feat. X',
    '2013', '10', 'Part 2', 'VIP',
]


def write_wav(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, 'wb') as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(44100)
        fh.writeframes(b'\x00\x01' * 512)
    return path


def write_mp3(path, **easy_tags):
    # MPEG-1 Layer III frames (128 kbps, 44.1 kHz, 417 bytes each): enough for
    # mutagen to open the file in easy mode, which retag-directory relies on.
    with open(path, 'wb') as fh:
        fh.write((b'\xff\xfb\x90\x00' + b'\x00' * 413) * 20)
    tags = EasyID3()
    for key, value in easy_tags.items():
        tags[key] = value
    tags.save(path)
    return path


def id3_text(path, frame_id):
    tags = WAVE(path).tags
    if tags is None or frame_id not in tags:
        return None
    return str(tags[frame_id])


class CatalogNumberTests(SimpleTestCase):
    def test_trailing_catalog_number_is_split_off_the_title(self):
        for catalog in CATALOG_NUMBERS:
            for token in (f'({catalog})', f'[{catalog}]'):
                with self.subTest(token=token):
                    self.assertEqual(
                        split_catalog_number(f'Turn It Up {token}'), ('Turn It Up', catalog),
                    )
                    self.assertEqual(clean_title(f'Turn It Up {token}'), 'Turn It Up')

    def test_version_year_and_number_parentheticals_stay_in_the_title(self):
        for text in NOT_CATALOG_NUMBERS:
            title = f'Turn It Up ({text})'
            with self.subTest(title=title):
                self.assertEqual(split_catalog_number(title), (title, ''))
                self.assertEqual(clean_title(title), title)

    def test_only_the_last_token_counts(self):
        self.assertEqual(
            clean_title('Turn It Up (Original Mix) (DDC011)'), 'Turn It Up (Original Mix)',
        )
        title = 'Turn It Up (DDC011) (Original Mix)'
        self.assertEqual(split_catalog_number(title), (title, ''))

    def test_a_title_that_is_only_a_catalog_number_is_not_emptied(self):
        self.assertEqual(split_catalog_number('(DDC011)'), ('(DDC011)', ''))


class DiscogsArtistSuffixTests(SimpleTestCase):
    def test_disambiguation_suffix_is_removed(self):
        self.assertEqual(clean_artist('Sisto (2)'), 'Sisto')
        self.assertEqual(clean_artist('Sisto (12)'), 'Sisto')
        self.assertEqual(clean_artist('Sisto (2), Rossiter (3)'), 'Sisto, Rossiter')
        self.assertEqual(clean_artist('Sisto (2) & Rossiter'), 'Sisto & Rossiter')

    def test_other_artist_text_is_kept(self):
        self.assertEqual(clean_artist('Sisto'), 'Sisto')
        self.assertEqual(clean_artist('Sisto (UK)'), 'Sisto (UK)')
        self.assertEqual(clean_artist('808 State'), '808 State')


class ArtistPrefixTests(SimpleTestCase):
    def test_unspaced_hyphen_splits_when_the_prefix_is_the_known_artist(self):
        self.assertEqual(_strip_artist_prefix('Sisto-Turn It Up', 'Sisto'), 'Turn It Up')
        self.assertEqual(_strip_artist_prefix('Brennan-Ital', 'Brennan'), 'Ital')

    def test_other_hyphenated_titles_are_left_alone(self):
        self.assertEqual(_strip_artist_prefix('Brennan-Ital', 'Rossiter'), 'Brennan-Ital')
        self.assertEqual(_strip_artist_prefix('Brennan-Ital', 'Brenn'), 'Brennan-Ital')

    def test_spaced_prefix_still_strips(self):
        self.assertEqual(
            _strip_artist_prefix("Urban Myths I Just Can't Help", 'Urban Myths'),
            "I Just Can't Help",
        )


class CleanMetadataCatalogTests(SimpleTestCase):
    def test_title_catalog_fills_an_empty_or_absent_catalog(self):
        out = _clean_metadata({'artist': 'Unknown Artist', 'title': 'Untitled A2 (GRAVITATE04)'})
        self.assertEqual((out['title'], out['catalog_number']), ('Untitled A2', 'GRAVITATE04'))

        out = _clean_metadata({'artist': 'Rossiter', 'title': 'Atom [SILKYDIGI05]', 'catalog_number': ''})
        self.assertEqual((out['title'], out['catalog_number']), ('Atom', 'SILKYDIGI05'))

    def test_same_catalog_is_only_stripped(self):
        out = _clean_metadata({
            'artist': 'Sisto (2)', 'title': 'Sisto-Turn It Up (DDC011)', 'catalog_number': 'DDC011',
        })
        self.assertEqual(
            (out['artist'], out['title'], out['catalog_number']), ('Sisto', 'Turn It Up', 'DDC011'),
        )

    def test_existing_different_catalog_is_kept(self):
        out = _clean_metadata({'title': 'Atom [SILKYDIGI05]', 'catalog_number': 'SILKY005'})
        self.assertEqual((out['title'], out['catalog_number']), ('Atom', 'SILKY005'))

    def test_no_catalog_key_is_added_when_the_title_has_none(self):
        # write_tags leaves a tag alone when its key is absent; keep it absent.
        out = _clean_metadata({'artist': 'Sisto', 'title': 'Turn It Up (Original Mix)'})
        self.assertNotIn('catalog_number', out)
        self.assertEqual(out['title'], 'Turn It Up (Original Mix)')


class DroppedFileTagAndRenameTests(TestCase):
    """tag_file + rename_file on the batch examples, metadata lookups mocked."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def process(self, original_filename, enriched=None):
        path = write_wav(os.path.join(self.tmpdir.name, 'source.wav'))
        item = PipelineItem.objects.create(
            original_filename=original_filename, current_path=path, stage='tagging',
        )
        with patch('organize.services.tagger.enrich_metadata',
                   return_value=dict(enriched) if enriched else None), \
                patch('organize.services.artwork.fetch_artwork', return_value=None):
            tag_file(item)
        rename_file(item)
        item.refresh_from_db()
        return item

    def assertResult(self, item, artist, title, catalog, filename):
        self.assertEqual(
            (item.artist, item.title, item.catalog_number, item.final_filename),
            (artist, title, catalog, filename),
        )
        path = item.current_path
        self.assertEqual(os.path.basename(path), filename)
        self.assertEqual(id3_text(path, 'TPE1'), artist or None)
        self.assertEqual(id3_text(path, 'TIT2'), title)
        self.assertEqual(id3_text(path, 'TXXX:CATALOGNUMBER'), catalog or None)

    def test_discogs_suffix_unspaced_artist_and_known_catalog(self):
        item = self.process('Sisto-Turn It Up (DDC011).flac', {
            'artist': 'Sisto (2)', 'title': 'Let Me See You Work It',
            'album': 'Let Me See You Work It', 'label': 'Dias De Campo Records',
            'catalog_number': 'DDC011', 'genre': 'Electronic', 'year': '', 'source': 'discogs',
        })
        self.assertResult(item, 'Sisto', 'Turn It Up', 'DDC011', 'Sisto - Turn It Up.wav')

    def test_parenthesised_catalog_without_a_metadata_match(self):
        item = self.process('Unknown Artist - Untitled A2 (GRAVITATE04).flac')
        self.assertResult(
            item, 'Unknown Artist', 'Untitled A2', 'GRAVITATE04', 'Unknown Artist - Untitled A2.wav',
        )

    def test_bracketed_catalog_is_kept_in_the_catalog_tag(self):
        item = self.process('Rossiter - Atom [SILKYDIGI05].flac')
        self.assertResult(item, 'Rossiter', 'Atom', 'SILKYDIGI05', 'Rossiter - Atom.wav')

        item = self.process('Wilba - Blast Blast [PT301].flac')
        self.assertResult(item, 'Wilba', 'Blast Blast', 'PT301', 'Wilba - Blast Blast.wav')

    def test_clean_match_is_unchanged(self):
        item = self.process('Youandewan - Quick Flight.flac', {
            'artist': 'Youandewan', 'title': 'Quick Flight', 'album': 'Quick Flight',
            'label': '', 'catalog_number': 'BRANE003', 'genre': '', 'year': '', 'source': 'discogs',
        })
        self.assertResult(item, 'Youandewan', 'Quick Flight', 'BRANE003', 'Youandewan - Quick Flight.wav')

    def test_metadata_catalog_wins_over_a_different_title_catalog(self):
        item = self.process('Rossiter - Atom [SILKYDIGI05].flac', {
            'artist': 'Rossiter', 'title': 'Atom', 'album': '', 'label': '',
            'catalog_number': 'SILKY005', 'genre': '', 'year': '', 'source': 'discogs',
        })
        self.assertResult(item, 'Rossiter', 'Atom', 'SILKY005', 'Rossiter - Atom.wav')

    def test_hyphenated_title_without_a_matching_artist_is_left_whole(self):
        item = self.process('Brennan-Ital.flac')
        self.assertResult(item, '', 'Brennan-Ital', '', 'Unknown Artist - Brennan-Ital.wav')

        item = self.process('Brennan-Ital.flac', {
            'artist': 'Rossiter', 'title': 'Brennan-Ital', 'album': '', 'label': '',
            'catalog_number': '', 'genre': '', 'year': '', 'source': 'discogs',
        })
        self.assertResult(item, 'Rossiter', 'Brennan-Ital', '', 'Rossiter - Brennan-Ital.wav')


class RetagEndpointCatalogTests(TestCase):
    """The bulk retag endpoints clean titles directly; the catalog must survive."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def test_retag_clean_moves_the_title_catalog_into_the_row_and_tags(self):
        path = write_wav(os.path.join(self.tmpdir.name, 'item.wav'))
        item = PipelineItem.objects.create(
            original_filename='item.wav', current_path=path, stage='ready',
            artist='Unknown Artist', title='Untitled A2 (GRAVITATE04)',
        )

        resp = self.client.post('/api/organize/pipeline/retag-clean/', data={'stage': 'ready'},
                                content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual((item.title, item.catalog_number), ('Untitled A2', 'GRAVITATE04'))
        self.assertEqual(id3_text(path, 'TIT2'), 'Untitled A2')
        self.assertEqual(id3_text(path, 'TXXX:CATALOGNUMBER'), 'GRAVITATE04')

    def test_retag_directory_keeps_the_catalog_and_respects_an_existing_one(self):
        empty = write_mp3(os.path.join(self.tmpdir.name, 'a.mp3'),
                          artist='Unknown Artist', title='Untitled A2 (GRAVITATE04)')
        tagged = write_mp3(os.path.join(self.tmpdir.name, 'b.mp3'),
                           artist='Rossiter', title='Atom [SILKYDIGI05]', catalognumber='SILKY005')

        resp = self.client.post('/api/organize/retag-directory/',
                                data={'path': self.tmpdir.name, 'dry_run': False},
                                content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['changed'], 2)
        a, b = EasyID3(empty), EasyID3(tagged)
        self.assertEqual((a['title'], a['catalognumber']), (['Untitled A2'], ['GRAVITATE04']))
        self.assertEqual((b['title'], b['catalognumber']), (['Atom'], ['SILKY005']))
