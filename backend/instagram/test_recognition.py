"""Credential-free, DB-free synthetic recognition fixtures."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from .recognition import explicit_candidates, release_candidates, _release_json

MODULE = explicit_candidates.__module__

RELEASE_ID = '12345678-1234-1234-1234-123456789abc'
RECORDING_ID = '87654321-1234-1234-1234-123456789abc'
CANDIDATE = {'artist': 'Fixture Artist', 'title': 'Fixture Track (Dub Mix)'}


def release_fixture():
    return {'id': RELEASE_ID, 'label-info': [{'catalog-number': 'FIX001', 'label': {'name': 'Fixture Label'}}],
            'media': [{'track-count': 1, 'tracks': [
                {'number': 'A1', 'title': CANDIDATE['title'], 'artist-credit': [{'name': CANDIDATE['artist']}],
                 'recording': {'id': RECORDING_ID, 'title': CANDIDATE['title']}}]}]}


class ExplicitTests(unittest.TestCase):
    def test_normalization_version_and_duplicate_evidence(self):
        caption = 'ID: Cafe\u0301 - Title (Dub Mix)'
        candidates, records = explicit_candidates(caption, 0, [{'id': '12', 'text': 'Track ID: CAFÉ - Title (Dub Mix)'}])
        self.assertEqual(candidates, [{'artist': 'Café', 'title': 'Title (Dub Mix)'}])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]['comment_id'], '12')

    def test_questions_prose_and_comment_numbering_do_not_identify(self):
        for text in ['great night - thanks', 'ID: Artist - Track?', 'ID: Artist - Track (?)', 'ID: Artist - Track? anyone', '1. Artist - Track', 'Release: Artist - Album EP']:
            with self.subTest(text=text):
                self.assertEqual(explicit_candidates('', 0, [{'id': '1', 'text': text}])[0], [])

    def test_disagreement_retains_records_for_audio_fallback(self):
        candidates, records = explicit_candidates('', 0, [
            {'id': '1', 'text': 'ID: Artist - Track (Dub Mix)'},
            {'id': '2', 'text': 'ID: Artist - Track (Original Mix)'}])
        self.assertEqual(candidates, [])
        self.assertEqual(len(records), 2)

    def test_numbered_caption_requires_complete_unambiguous_carousel(self):
        caption = 'Tracklist:\n1. Artist - One\n2. Artist - Two'
        self.assertEqual(explicit_candidates(caption, 1, media_count=2)[0], [{'artist': 'Artist', 'title': 'Two'}])
        for count in [0, 1, 3]:
            self.assertEqual(explicit_candidates(caption, 0, media_count=count)[0], [])
        self.assertEqual(explicit_candidates(caption + '\n2. Artist - Other', 1, media_count=2)[0], [])

    def test_explicit_slide_mapping_does_not_guess_position(self):
        for source in ['caption', 'comment']:
            caption = 'Video 2: Artist - Track (Dub Mix)' if source == 'caption' else ''
            comments = [{'id': '2', 'text': 'Slide 2: ID: Artist - Track (Dub Mix)'}] if source == 'comment' else []
            self.assertEqual(explicit_candidates(caption, 0, comments, media_count=0)[0], [])
            self.assertEqual(explicit_candidates(caption, 1, comments, media_count=0)[0],
                             [{'artist': 'Artist', 'title': 'Track (Dub Mix)'}])

    def test_bounded_comments_and_records(self):
        comments = [{'id': str(i), 'text': 'ID: Artist - Track'} for i in range(100)]
        self.assertEqual(len(explicit_candidates('', 0, comments)[1]), 10)
        comments[19]['text'] = 'ID: Other - Recording'
        self.assertEqual(explicit_candidates('', 0, comments)[0], [])


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load the real pure preview helper without wanted.services' eager
        # Django importer. Local fixture runs never initialize Django or a DB.
        import importlib.util
        from pathlib import Path
        import sys
        spec = importlib.util.spec_from_file_location('fixture_preview', Path(__file__).parents[1] / 'wanted/services/preview.py')
        preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preview)
        modules = patch.dict(sys.modules, {'wanted.services.preview': preview})
        modules.start()
        cls.addClassCleanup(modules.stop)

    def resolve(self, detail=None, caption='Catalog: FIX001\nLabel: Fixture Label\nTrack: A1', count=1, position=0):
        with patch(f'{MODULE}._release_json', side_effect=[
                {'count': 1, 'releases': [{'id': RELEASE_ID}]}, detail if detail is not None else release_fixture()]) as provider, \
                patch('time.sleep'):
            result = release_candidates(caption, position, count)
        return result, provider

    def test_exact_catalog_label_recording_and_version_evidence(self):
        (candidates, records), provider = self.resolve()
        self.assertEqual(candidates, [CANDIDATE])
        self.assertIn(RELEASE_ID, records[0]['text'])
        self.assertIn(RECORDING_ID, records[0]['text'])
        self.assertIn('A1', records[0]['text'])
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(provider.call_args_list[0].args[1]['query'], 'catno:"FIX001" AND label:"Fixture Label"')

    def test_bracket_and_hashtag_catalog_cues_use_same_exact_lookup(self):
        for caption in ['Fixture release [FIX001]\nTrack: A1', '#FIX001\nTrack: A1']:
            result, provider = self.resolve(caption=caption)
            self.assertEqual(result[0], [CANDIDATE])
            self.assertEqual(provider.call_args_list[0].args[1]['query'], 'catno:"FIX001"')

    def test_title_only_invalid_or_conflicting_catalog_does_not_call_provider(self):
        for caption in ['Release: Artist - Album EP', 'Catalog: 1234', 'Catalog: FIX001\nCatalog: OTHER002',
                        'Catalog: FIX001\nTrack: A1']:
            count = 2 if 'Track:' in caption else 1
            with patch(f'{MODULE}._release_json') as provider:
                self.assertEqual(release_candidates(caption, 0, count), ([], []))
                provider.assert_not_called()

    def test_ambiguous_search_never_picks_first(self):
        for search in [{'count': 2, 'releases': [{'id': RELEASE_ID}]}, {}, {'count': 1, 'releases': [{'id': '../../evil'}]}]:
            with patch(f'{MODULE}._release_json', return_value=search) as provider:
                self.assertEqual(release_candidates('Catalog: FIX001', 0, 1), ([], []))
                provider.assert_called_once()

    def test_wrong_catalog_label_version_or_missing_credit_needs_audio(self):
        changes = [lambda d: d['media'][0]['tracks'][0]['recording'].update({'title': 'Fixture Track (Radio Edit)'}),
                   lambda d: d['label-info'][0].update({'catalog-number': 'FIX002'}),
                   lambda d: d['label-info'][0]['label'].update({'name': 'Other Label'}),
                   lambda d: d['media'][0]['tracks'][0]['recording'].update({'disambiguation': 'radio edit'}),
                   lambda d: d['media'][0]['tracks'][0].update({'artist-credit': []}),
                   lambda d: d['media'][0]['tracks'][0]['recording'].update({'id': ''}),
                   lambda d: d.update({'media': [*d['media'], copy.deepcopy(d['media'][0])]}),
                   lambda d: d['media'][0].update({'track-count': 10})]
        for change in changes:
            detail = release_fixture(); change(detail)
            self.assertEqual(self.resolve(detail)[0], ([], []))

    def test_multi_track_release_needs_declared_carousel_order(self):
        detail = release_fixture()
        second = copy.deepcopy(detail['media'][0]['tracks'][0]); second.update(number='A2', title='Other (Original Mix)'); second['recording']['title'] = second['title']
        detail['media'][0]['tracks'].append(second); detail['media'][0]['track-count'] = 2
        self.assertEqual(self.resolve(detail, 'Catalog: FIX001', count=1)[0], ([], []))
        self.assertEqual(self.resolve(detail, 'Catalog: FIX001', count=2)[0], ([], []))
        result, _ = self.resolve(detail, 'Catalog: FIX001\nCarousel: tracklist order', count=2, position=1)
        self.assertEqual(result[0], [{'artist': 'Fixture Artist', 'title': 'Other (Original Mix)'}])
        self.assertEqual(self.resolve(detail, 'Catalog: FIX001\nTrack: Z9')[0], ([], []))

    def test_provider_transport_malformed_and_schema_failures_are_safe(self):
        import requests
        for failure in [requests.Timeout(), requests.HTTPError(), ValueError(), TypeError()]:
            with patch(f'{MODULE}._release_json', side_effect=failure):
                self.assertEqual(release_candidates('Catalog: FIX001', 0, 1), ([], []))
        for detail in [[], None, {'media': None}, {'id': RELEASE_ID, 'label-info': [None]}]:
            with patch(f'{MODULE}._release_json', side_effect=[
                    {'count': 1, 'releases': [{'id': RELEASE_ID}]}, detail]), patch('time.sleep'):
                self.assertEqual(release_candidates('Catalog: FIX001', 0, 1), ([], []))

    def test_fixed_origin_timeout_redirect_body_caps_and_close(self):
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock()
        response.iter_content.return_value = [json.dumps({'count': 0}).encode()]
        with patch('requests.get', return_value=response) as get, patch(f'{MODULE}._PROVIDER_NEXT', 0):
            self.assertEqual(_release_json('', {'query': 'catno:"FIX001"'}), {'count': 0})
            self.assertEqual(_release_json('', {}), {})  # no immediate retry
            get.assert_called_once()
            self.assertEqual(get.call_args.args[0], 'https://musicbrainz.org/ws/2/release/')
            self.assertEqual(get.call_args.kwargs['timeout'], (3, 5))
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
            self.assertNotIn('Authorization', get.call_args.kwargs['headers'])
        response.__exit__.assert_called_once()
        response.iter_content.return_value = [b'x' * (512 * 1024 + 1)]
        with patch('requests.get', return_value=response), patch(f'{MODULE}._PROVIDER_NEXT', 0):
            self.assertEqual(_release_json('', {}), {})


class CommentFallbackTests(unittest.TestCase):
    def test_existing_connector_uses_audio_for_ambiguous_cues_but_not_one_explicit_id(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from tools.instagram import connector as c
        for comments, use_audio in [
                ([{'id': '1', 'text': 'ID: Artist - Track (Dub Mix)'},
                  {'id': '2', 'text': 'ID: Artist - Track (Radio Edit)'}], True),
                ([{'id': '1', 'text': 'Release: Artist - Album EP'}], True),
                ([{'id': '1', 'text': 'ID: Artist - Track?'}], True),
                ([{'id': '1', 'text': 'ID: Artist - Track (Dub Mix)'}], False)]:
            with self.subTest(comments=comments):
                api = Mock()
                jobs = iter([{'id': 7, 'position': 0, 'post_instagram_id': '100', 'token': 'fixture', 'action': 'recognize'}, None])
                def post(endpoint, *args, **kwargs):
                    if endpoint == 'sync/': return {'media': []}
                    if endpoint == 'claim/': return next(jobs, None)
                    return {'ok': True}
                api.post.side_effect = post
                def download(url, path):
                    path.write_bytes(b'synthetic-only')
                    return 'video/mp4'
                with patch.dict('os.environ', {'DRAIN_TOKEN': 'synthetic-only'}), \
                        patch.object(c, 'API', return_value=api), \
                        patch.object(c, 'instagram_session'), \
                        patch.object(c, 'read_collection', return_value=({'posts': []}, {})), \
                        patch.object(c, 'read_comments', return_value=comments), \
                        patch.object(c, 'read_source', return_value='https://example.test/fixture'), \
                        patch.object(c, 'download', side_effect=download) as transport, \
                        patch.object(c, 'ffmpeg_path', return_value='fixture-ffmpeg'), \
                        patch.object(c, 'recognize', new_callable=AsyncMock, return_value=[CANDIDATE]) as shazam:
                    c.run(SimpleNamespace(api='https://example.test', profile='fixture', fixture=None, work_seconds=0))
                self.assertEqual(transport.call_count, int(use_audio))
                self.assertEqual(shazam.await_count, int(use_audio))
                completion = next(call.args[1] for call in api.post.call_args_list if call.args[0] == 'media/7/complete/')
                self.assertEqual(completion['status'], 'identified')
                self.assertEqual(completion['recognition']['source'], 'shazam' if use_audio else 'comment')
                self.assertEqual(completion['recognition']['comments'], 'partial')
