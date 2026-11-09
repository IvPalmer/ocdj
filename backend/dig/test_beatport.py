import os
from unittest.mock import patch

from rest_framework.test import APITestCase

from wanted.models import WantedItem
from .serializers import DigAddSerializer, DigBatchSerializer


class BeatportCaptureTests(APITestCase):
    def setUp(self):
        self.token = patch.dict(os.environ, {'DIG_TOKEN': 'synthetic-test-token'})
        self.token.start()
        self.addCleanup(self.token.stop)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer synthetic-test-token')
        self.payload = {
            'artist': 'Fixture Artist', 'title': 'Fixture Track (Extended)',
            'source_site': 'beatport',
            'source_url': 'https://www.beatport.com/track/fixture/123',
        }

    def test_track_persists_metadata_and_original_source_url(self):
        response = self.client.post('/add/', self.payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data['created'])
        item = WantedItem.objects.get()
        self.assertEqual(item.artist, self.payload['artist'])
        self.assertEqual(item.title, self.payload['title'])
        self.assertEqual(item.notes, self.payload['source_url'])
        # Existing Dig ownership is retained, with the original platform URL in notes.
        self.assertEqual(item.source.source_type, 'dig')
        self.assertEqual(response.data['item']['id'], item.pk)
        self.assertEqual(response.data['item']['notes'], item.notes)

    def test_release_accepts_blank_title_and_persists_release_name(self):
        payload = dict(self.payload, title='', release_name='Fixture EP',
                       source_url='https://www.beatport.com/release/fixture/456')
        response = self.client.post('/add/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        item = WantedItem.objects.get()
        self.assertEqual(item.title, '')
        self.assertEqual(item.release_name, 'Fixture EP')
        self.assertEqual(item.notes, payload['source_url'])

    def test_bandcamp_metadata_is_stored_separately_from_track_identity(self):
        payload = dict(self.payload, source_site='bandcamp', title='Fixture Recording',
                       source_url='https://label.bandcamp.com/track/fixture',
                       release_name='Fixture EP', label='Fixture Label', catalog_number='FD-003',
                       isrc='GBABC2101234', notes='Track number: 2')
        self.assertEqual(self.client.post('/add/', payload, format='json').status_code, 201)
        item = WantedItem.objects.get()
        self.assertEqual((item.title, item.release_name, item.label, item.catalog_number, item.cleanup_isrc),
                         ('Fixture Recording', 'Fixture EP', 'Fixture Label', 'FD-003', 'GBABC2101234'))
        self.assertEqual(item.notes, 'Track number: 2\n' + payload['source_url'])

    def test_release_only_dedup_does_not_swallow_a_track_on_that_release(self):
        payload = dict(self.payload, source_site='bandcamp', artist='', title='', release_name='Fixture EP')
        self.assertEqual(self.client.post('/add/', payload, format='json').status_code, 201)
        self.assertEqual(self.client.post('/add/', payload, format='json').status_code, 200)
        self.assertEqual(self.client.post('/add/', dict(payload, title='A track'), format='json').status_code, 201)

    def test_existing_track_dedup_is_retained_across_sources(self):
        existing = WantedItem.objects.create(artist=self.payload['artist'], title=self.payload['title'])
        response = self.client.post('/add/', self.payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['created'])
        self.assertTrue(response.data['duplicate'])
        self.assertEqual(response.data['duplicate_of']['id'], existing.pk)
        self.assertEqual(response.data['fuzzy_score'], 100)
        self.assertEqual(WantedItem.objects.count(), 1)

    def test_unknown_source_and_empty_recording_remain_validation_errors(self):
        for payload, field in [
            (dict(self.payload, source_site='unknown'), 'source_site'),
            (dict(self.payload, artist='', title=''), 'non_field_errors'),
        ]:
            with self.subTest(field=field):
                response = self.client.post('/add/', payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
        self.assertFalse(WantedItem.objects.exists())

    def test_missing_and_wrong_bearer_tokens_still_fail(self):
        for header, error in [('', 'missing bearer token'), ('Bearer wrong', 'invalid token')]:
            with self.subTest(header=header):
                self.client.credentials(HTTP_AUTHORIZATION=header)
                response = self.client.post('/add/', self.payload, format='json')
                self.assertEqual(response.status_code, 401, response.data)
                self.assertEqual(response.data['error'], error)
        self.assertFalse(WantedItem.objects.exists())

    def test_unconfigured_backend_still_fails_closed(self):
        with patch.dict(os.environ, {'DIG_TOKEN': ''}):
            response = self.client.post('/add/', self.payload, format='json')
        self.assertEqual(response.status_code, 503, response.data)
        self.assertIn('disabled', response.data['error'])
        self.assertFalse(WantedItem.objects.exists())

    def test_existing_single_sources_and_batch_scope_are_preserved(self):
        for source in ['discogs', 'bandcamp', 'youtube', 'soundcloud', 'spotify']:
            with self.subTest(source=source):
                serializer = DigAddSerializer(data=dict(self.payload, source_site=source))
                self.assertTrue(serializer.is_valid(), serializer.errors)
        batch = DigBatchSerializer(data={
            'source_site': 'beatport', 'items': [{'artist': 'Fixture', 'title': 'Track'}],
        })
        self.assertFalse(batch.is_valid())
        self.assertIn('source_site', batch.errors)
