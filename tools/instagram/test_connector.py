import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
from tools.instagram.connector import parse_posts, cdn_url, ConnectorError, read_collection, API, run


class ConnectorTests(unittest.TestCase):
    def test_all_carousel_children_and_response_order(self):
        items = [{'pk': 20, 'code': 'abc', 'taken_at': 1, 'carousel_media': [
            {'pk': 201, 'media_type': 1},
            {'pk': 202, 'media_type': 2, 'video_versions': [{'url': 'https://s.cdninstagram.com/a.mp4', 'width': 640}]},
            {'pk': 203, 'media_type': 2, 'video_versions': [{'url': 'https://s.cdninstagram.com/b.mp4', 'width': 640}]}]},
            {'pk': 10, 'code': 'def', 'taken_at': 9999, 'media_type': 1}]
        data, urls = parse_posts(items)
        self.assertEqual([p['id'] for p in data['posts']], ['20', '10'])
        self.assertEqual([c['kind'] for c in data['posts'][0]['children']], ['image', 'video', 'video'])
        self.assertEqual(set(urls), {'202', '203'})
        self.assertNotIn('video_versions', str(data))
        self.assertNotIn('cdninstagram', str(data))

    def test_arbitrary_hosts_and_redirect_credentials_disallowed(self):
        for url in ['http://s.cdninstagram.com/v', 'https://localhost/v', 'https://cdninstagram.com.evil/v',
                    'https://s.cdninstagram.com:444/v', 'https://user:pass@s.cdninstagram.com/v']:
            with self.assertRaises(ConnectorError): cdn_url(url)
        with patch('tools.instagram.connector.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ConnectorError): cdn_url('https://s.cdninstagram.com/a')
        with self.assertRaises(ConnectorError): API('http://example.com', 'never-send')

    def test_session_rate_and_restriction_are_explicit_without_body_leak(self):
        for code, status in [(302, 'session-expired'), (401, 'session-expired'), (403, 'restricted'), (429, 'rate-limited')]:
            session = Mock(); session.get.return_value.status_code = code
            with self.assertRaisesRegex(ConnectorError, status): read_collection(session)
            self.assertFalse(session.get.call_args.kwargs['allow_redirects'])

    def test_run_completes_error_then_stops_for_each_instagram_stop_signal(self):
        for code, status in [(401, 'session-expired'), (403, 'restricted'), (429, 'rate-limited')]:
            with self.subTest(status=status):
                initial = Mock(status_code=200)
                initial.json.return_value = {'status': 'ok', 'items': []}
                session = Mock()
                session.get.side_effect = [initial, Mock(status_code=code)]
                api = Mock()
                job = {'id': 7, 'instagram_id': '202', 'post_instagram_id': '20', 'position': 0, 'token': 'lease-token'}
                api.post.side_effect = lambda endpoint, *a, **kw: (
                    {'media': []} if endpoint == 'sync/' else
                    job if endpoint == 'claim/' and api.post.call_args_list.count(call('claim/')) == 1
                    else None)
                args = SimpleNamespace(api='https://example.test', profile='Profile 1',
                                       fixture=None, work_seconds=0)
                with patch.dict('os.environ', {'DRAIN_TOKEN': 'test-only'}), \
                     patch('tools.instagram.connector.API', return_value=api), \
                     patch('tools.instagram.connector.ffmpeg_path', return_value='fixture-ffmpeg'), \
                     patch('tools.instagram.connector.instagram_session', return_value=session), \
                     patch('tools.instagram.connector.download') as download, \
                     patch('tools.instagram.connector.recognize') as recognize:
                    with self.assertRaisesRegex(ConnectorError, '^'+status+'$'):
                        run(args)
                self.assertEqual(session.get.call_count, 2)  # initial sync + failing job refresh
                download.assert_not_called()
                recognize.assert_not_called()
                self.assertEqual(api.post.call_args_list, [
                    call('sync/', {'collection_id': '18117783274346248', 'posts': []}),
                    call('heartbeat/', {'status': 'ready'}),
                    call('heartbeat/', {'status': 'ready'}),
                    call('claim/'),
                    call('media/7/complete/', {'token': 'lease-token', 'status': 'error', 'candidates': [], 'recognition': {'source': 'shazam', 'certainty': 'tentative', 'comments': 'not-read', 'outcome': 'error'}}),
                    call('heartbeat/', {'status': status}),
                ])

    def test_full_cursor_walk_deduplicates_in_saved_order(self):
        def page(ids, more, cursor=None):
            response = Mock(status_code=200)
            response.json.return_value = {'status': 'ok', 'items': [
                {'pk': i, 'code': f'post{i}', 'media_type': 1} for i in ids],
                'more_available': more, 'next_max_id': cursor}
            return response
        session = Mock()
        session.get.side_effect = [page(range(10, 0, -1), True, 'next'), page([1, 20, 21], False)]
        data, _ = read_collection(session)
        self.assertEqual([p['id'] for p in data['posts']], [str(i) for i in range(10, 0, -1)] + ['20', '21'])
        self.assertEqual(session.get.call_args.kwargs['params']['max_id'], 'next')

    def test_metadata_import_does_not_download_or_require_ffmpeg(self):
        api = Mock()
        api.post.side_effect = lambda endpoint, *a, **kw: {'media': [dict(id=1, kind='video')]} if endpoint == 'sync/' else None
        args = SimpleNamespace(api='https://example.test', profile='Profile 1', fixture=None, work_seconds=0)
        with patch.dict('os.environ', {'DRAIN_TOKEN': 'fixture-only'}), \
             patch('tools.instagram.connector.API', return_value=api), \
             patch('tools.instagram.connector.instagram_session'), \
             patch('tools.instagram.connector.read_collection', return_value=({'posts': []}, {})), \
             patch('tools.instagram.connector.download') as download, \
             patch('tools.instagram.connector.ffmpeg_path') as ffmpeg:
            run(args)
        download.assert_not_called(); ffmpeg.assert_not_called()
        self.assertEqual(api.post.call_args_list[0], call('sync/', {'posts': []}))

    def test_source_audio_extraction_no_match_and_transport_failure(self):
        import asyncio
        import tempfile
        from pathlib import Path
        from backend.instagram.recognition import recognize
        from unittest.mock import AsyncMock, MagicMock
        shazam = MagicMock()
        shazam.__aenter__.return_value = shazam
        shazam.recognize = AsyncMock(return_value={})
        def extract(command, **kwargs):
            Path(command[-1]).write_bytes(b'x'*1001)
            return SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict('sys.modules', {'shazamio': SimpleNamespace(Shazam=lambda: shazam)}), \
             patch('backend.instagram.recognition.subprocess.run', side_effect=extract) as ffmpeg:
            self.assertEqual(asyncio.run(recognize(Path(directory)/'source.mp4', directory, 'ffmpeg')), [])
            self.assertEqual(shazam.recognize.await_count, 3)
            shazam.__aenter__.assert_awaited_once()
            shazam.__aexit__.assert_awaited_once_with(None, None, None)
            self.assertTrue(all('-vn' in c.args[0] for c in ffmpeg.call_args_list))
            shazam.recognize.side_effect = TimeoutError('fixture service timeout')
            with self.assertRaises(TimeoutError):
                asyncio.run(recognize(Path(directory)/'source.mp4', directory, 'ffmpeg'))
            self.assertEqual(shazam.__aexit__.await_count, 2)
            self.assertIs(shazam.__aexit__.await_args.args[0], TimeoutError)

    def test_twenty_two_pages_full_collection_metadata_without_download(self):
        items = [{'pk': i, 'code': f'post{i}', 'carousel_media': [
            {'pk': 100000+i*20+j, 'media_type': 2} for j in range(5 if i < 208 else 4)]}
            for i in range(256)]
        responses = []
        for offset in range(0, 256, 12):
            response = Mock(status_code=200)
            response.json.return_value = dict(status='ok', items=items[offset:offset+12],
                more_available=offset+12 < 256, next_max_id=str(offset+12))
            responses.append(response)
        session = Mock(); session.get.side_effect = responses
        data, urls = read_collection(session)
        self.assertEqual(len(data['posts']), 256)
        self.assertEqual(sum(len(p['children']) for p in data['posts']), 1232)
        self.assertEqual(session.get.call_count, 22)
        self.assertEqual(urls, {})

    def test_incomplete_or_cyclic_snapshot_fails_before_sync(self):
        for cursor in [None, 'loop']:
            session = Mock()
            session.get.return_value.status_code = 200
            session.get.return_value.json.return_value = {'status': 'ok', 'items': [],
                'more_available': True, 'next_max_id': cursor}
            with self.assertRaisesRegex(ConnectorError, 'incomplete'): read_collection(session)
            self.assertLessEqual(session.get.call_count, 2)

    def test_explicit_ids_preserve_versions_and_numbered_carousel_position(self):
        from backend.instagram.recognition import explicit_candidates
        candidates, evidence = explicit_candidates('Tracklist:\n1. Artist - Title (Dub Mix)\n2. Other - Track', 0)
        self.assertEqual(candidates, [{'artist': 'Artist', 'title': 'Title (Dub Mix)'}])
        self.assertEqual(evidence[0]['source'], 'caption')
        candidates, evidence = explicit_candidates('Great night - thank you!', 0, [{'id': '12', 'text': 'ID: DJ - Track'}])
        self.assertEqual(candidates, [{'artist': 'DJ', 'title': 'Track'}])
        self.assertEqual(evidence[0]['comment_id'], '12')


# All bytes, IDs, URLs and API/Instagram responses below are synthetic.
class ThumbnailTests(unittest.TestCase):
    def test_parser_prefers_parent_image_versions_then_first_child_poster(self):
        from tools.instagram import connector as c
        def versions(name):
            return {'candidates': [
                {'url': f'https://s.cdninstagram.com/{name}-large', 'width': 1080, 'height': 1080},
                {'url': f'https://s.cdninstagram.com/{name}-small', 'width': 240, 'height': 320},
                {'url': 'https://s.cdninstagram.com/oversize', 'width': 5000, 'height': 5000}]}
        items = [{'pk': 1, 'code': 'one', 'media_type': 2, 'image_versions2': versions('parent'),
                  'video_versions': [{'url': 'https://s.cdninstagram.com/video', 'width': 640}]},
                 {'pk': 2, 'code': 'two', 'carousel_media': [
                     {'pk': 21, 'media_type': 2},
                     {'pk': 22, 'media_type': 2, 'image_versions2': versions('poster')},
                     {'pk': 23, 'media_type': 1, 'image_versions2': versions('later')}]},
                 {'pk': 3, 'code': 'three', 'media_type': 2}]
        posters = {}
        data, sources = c.parse_posts(items, thumbnail_urls=posters)
        self.assertEqual(posters, {'1': 'https://s.cdninstagram.com/parent-small',
                                   '2': 'https://s.cdninstagram.com/poster-small'})
        self.assertEqual(sources['1'], 'https://s.cdninstagram.com/video')
        self.assertEqual([p['id'] for p in data['posts']], ['1', '2', '3'])
        self.assertNotIn('cdninstagram', str(data))

    @staticmethod
    def image(fmt='PNG', size=(640, 480)):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new('RGB', size, (35, 90, 145)).save(buf, format=fmt)
        return buf.getvalue()

    def test_conversion_scales_real_pixels_to_small_metadata_free_jpeg(self):
        from io import BytesIO
        from PIL import Image
        from tools.instagram import connector as c
        source = Image.new('RGB', (640, 480), (35, 90, 145))
        exif = Image.Exif(); exif[270] = 'synthetic private metadata'
        encoded = BytesIO()
        source.save(encoded, format='PNG', exif=exif, icc_profile=b'synthetic profile')
        jpeg = c.thumbnail_jpeg(encoded.getvalue())
        self.assertLessEqual(len(jpeg), 131072)
        with Image.open(BytesIO(jpeg)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (320, 240))
            self.assertEqual(set(image.info) - {'jfif', 'jfif_version', 'jfif_unit', 'jfif_density'}, set())
            self.assertTrue(all(abs(a-b) <= 3 for a, b in zip(image.getpixel((10, 10)), (35, 90, 145))))

    def test_conversion_rejects_video_spoof_truncation_and_pixel_bomb(self):
        from tools.instagram import connector as c
        for raw in [b'\0\0\0\x18ftypmp42', self.image()[:80], self.image(size=(2001, 2000))]:
            with self.subTest(length=len(raw)), self.assertRaises(c.ConnectorError):
                c.thumbnail_jpeg(raw)

    def test_missing_only_batches_16_and_partial_results_are_not_retried(self):
        import base64
        from tools.instagram import connector as c
        ids = [str(i) for i in range(19)]
        sync = {'sync_token': 'synthetic-token', 'thumbnail_posts': [
            {'post_id': ident, 'available': ident == '0'} for ident in ids]}
        capture = c.ThumbnailCapture(sync, {i: f'https://s.cdninstagram.com/{i}' for i in ids})
        api = Mock()
        api.post.return_value = {'results': [{'post_id': '1', 'status': 'retry'},
                                             {'post_id': '2', 'status': 'inactive'}]}
        with patch('tools.instagram.connector.capture_thumbnail', return_value=b'jpeg') as fetch:
            while capture.step(api):
                pass
        self.assertEqual(fetch.call_count, 18)
        self.assertNotIn(call('https://s.cdninstagram.com/0'), fetch.call_args_list)
        batches = [args.args[1] for args in api.post.call_args_list]
        self.assertEqual([len(batch['thumbnails']) for batch in batches], [16, 2])
        self.assertEqual([x['post_id'] for batch in batches for x in batch['thumbnails']], ids[1:])
        self.assertTrue(all(set(batch) == {'sync_token', 'thumbnails'} for batch in batches))
        self.assertEqual(base64.b64decode(batches[0]['thumbnails'][0]['jpeg']), b'jpeg')
        self.assertNotIn('https:', str(batches))

    def test_legacy_warm_and_missing_sources_do_no_fetch(self):
        from tools.instagram import connector as c
        for sync in [{}, {'sync_token': 'token'}, {'thumbnail_posts': []},
                     {'sync_token': 'token', 'thumbnail_posts': [
                         {'post_id': '1', 'available': True}, {'post_id': '2', 'available': False}]}]:
            with patch('tools.instagram.connector.capture_thumbnail') as fetch:
                capture = c.ThumbnailCapture(sync, {'1': 'https://s.cdninstagram.com/warm'})
                self.assertFalse(capture.step(Mock()))
                fetch.assert_not_called()

    def test_stale_batch_stops_all_further_capture(self):
        from tools.instagram import connector as c
        sync = {'sync_token': 'stale', 'thumbnail_posts': [
            {'post_id': str(i), 'available': False} for i in range(20)]}
        capture = c.ThumbnailCapture(sync, {str(i): 'https://s.cdninstagram.com/poster' for i in range(20)})
        api = Mock(); api.post.return_value = None  # API's 409 sentinel
        with patch('tools.instagram.connector.capture_thumbnail', return_value=b'jpeg') as fetch:
            while capture.step(api):
                pass
        self.assertEqual(fetch.call_count, 16)
        self.assertEqual(api.post.call_count, 1)

    def test_full_size_batch_fits_backend_json_limit_and_409_uses_real_api_sentinel(self):
        import json
        from tools.instagram import connector as c
        sync = {'sync_token': 't' * 512, 'thumbnail_posts': [
            {'post_id': str(i), 'available': False} for i in range(17)]}
        capture = c.ThumbnailCapture(sync, {str(i): 'https://s.cdninstagram.com/image' for i in range(17)})
        api = c.API('https://example.test', 'synthetic-bearer')
        api.session.post = Mock(return_value=Mock(status_code=409))
        with patch('tools.instagram.connector.capture_thumbnail', return_value=b'x' * 131072) as fetch:
            while capture.step(api): pass
        self.assertEqual(fetch.call_count, 16)
        self.assertEqual(api.session.post.call_count, 1)
        sent = api.session.post.call_args.kwargs
        self.assertLess(len(json.dumps(sent['json']).encode()), 3*1024*1024)
        self.assertEqual(sent['timeout'], 10)
        self.assertFalse(sent['allow_redirects'])

    def test_budget_flushes_successes_without_starting_more_images(self):
        from tools.instagram import connector as c
        with patch('tools.instagram.connector.time.monotonic', return_value=0):
            capture = c.ThumbnailCapture({'sync_token': 'token', 'thumbnail_posts': [
                {'post_id': '1', 'available': False}, {'post_id': '2', 'available': False}]},
                {'1': 'https://s.cdninstagram.com/1', '2': 'https://s.cdninstagram.com/2'})
            api = Mock()
            with patch('tools.instagram.connector.capture_thumbnail', return_value=b'jpeg') as fetch:
                self.assertTrue(capture.step(api))
                with patch('tools.instagram.connector.time.monotonic', return_value=301):
                    self.assertFalse(capture.step(api))
                self.assertEqual(fetch.call_count, 1)
        self.assertEqual(api.post.call_args.args[1]['thumbnails'][0]['post_id'], '1')

    def test_bad_image_is_skipped_but_upstream_stop_signals_escape(self):
        from tools.instagram import connector as c
        sync = {'sync_token': 'token', 'thumbnail_posts': [{'post_id': '1', 'available': False}]}
        for status in ['error', 'restricted', 'session-expired', 'rate-limited']:
            capture = c.ThumbnailCapture(sync, {'1': 'https://s.cdninstagram.com/poster'})
            with patch('tools.instagram.connector.capture_thumbnail', side_effect=c.ConnectorError(status)):
                if status in c.STOP_STATUSES:
                    with self.assertRaisesRegex(c.ConnectorError, status): capture.step(Mock())
                else:
                    capture.step(Mock())
                    self.assertFalse(capture.step(Mock()))


class ThumbnailDownloadTests(unittest.TestCase):
    def response(self, raw=b'bytes', status=200, mime='image/jpeg', **headers):
        from io import BytesIO
        from unittest.mock import MagicMock
        response = MagicMock(status_code=status, headers={'Content-Type': mime, **headers})
        response.raw = BytesIO(raw)
        response.__enter__.return_value = response
        return response

    def download(self, response):
        import tempfile
        from pathlib import Path
        from tools.instagram import connector as c
        with tempfile.TemporaryDirectory() as directory, \
             patch('tools.instagram.connector.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]), \
             patch('tools.instagram.connector.requests.get', return_value=response) as request:
            dest = Path(directory) / 'poster'
            mime = c.download('https://s.cdninstagram.com/image', dest, image_only=True)
            self.assertEqual(mime, 'image/jpeg')
            self.assertFalse(request.call_args.kwargs['allow_redirects'])
            self.assertEqual(request.call_args.kwargs['headers'], {'Accept-Encoding': 'identity'})
            self.assertNotIn('cookies', request.call_args.kwargs)
            self.assertNotIn('auth', request.call_args.kwargs)
            self.assertEqual(request.call_args.kwargs['timeout'], (3, 3))
            return dest.read_bytes()

    def test_cookie_free_image_download_and_reject_video_redirect_and_http_restrictions(self):
        self.assertEqual(self.download(self.response()), b'bytes')
        for code, status in [(302, 'restricted'), (401, 'session-expired'), (403, 'restricted'), (429, 'rate-limited')]:
            with self.subTest(code=code), self.assertRaisesRegex(ConnectorError, status):
                self.download(self.response(status=code))
        with self.assertRaises(ConnectorError): self.download(self.response(mime='video/mp4'))

    def test_older_streaming_response_still_uses_deadline_checked_chunks(self):
        response = self.response()
        response.raw = SimpleNamespace()  # old urllib3, without read1
        response.iter_content.return_value = iter([b'a', b'b'])
        self.assertEqual(self.download(response), b'ab')
        response.iter_content.assert_called_once_with(1)

    def test_source_bytes_declared_length_encoding_and_total_deadline_caps(self):
        from tools.instagram import connector as c
        for response in [self.response(b'x' * (2*1024*1024+1)),
                         self.response(**{'Content-Length': '2097153'}),
                         self.response(**{'Content-Encoding': 'gzip'})]:
            with self.assertRaises(ConnectorError): self.download(response)
        with patch('tools.instagram.connector.time.monotonic', side_effect=[0, 11]), \
             self.assertRaises(ConnectorError):
            self.download(self.response())

    def test_low_level_transport_errors_become_sanitized_per_image_failures(self):
        from urllib3.exceptions import ProtocolError
        from tools.instagram import connector as c
        with patch('tools.instagram.connector.download', side_effect=ProtocolError('private source text')):
            with self.assertRaisesRegex(ConnectorError, '^error$'):
                c.capture_thumbnail('https://s.cdninstagram.com/poster')

    def test_capture_converts_and_deletes_transient_source_on_success_and_failure(self):
        from pathlib import Path
        from tools.instagram import connector as c
        locations = []
        def fetch(url, dest, **kwargs):
            self.assertTrue(kwargs['image_only'])
            locations.append(dest)
            dest.write_bytes(ThumbnailTests.image())
        with patch('tools.instagram.connector.download', side_effect=fetch):
            self.assertTrue(c.capture_thumbnail('https://s.cdninstagram.com/image').startswith(b'\xff\xd8'))
            with patch('tools.instagram.connector.thumbnail_jpeg', side_effect=ConnectorError('error')):
                with self.assertRaises(ConnectorError): c.capture_thumbnail('https://s.cdninstagram.com/image')
        self.assertTrue(all(not p.parent.exists() for p in locations))


class ThumbnailRunTests(unittest.TestCase):
    def run_connector(self, sync, download_effect=None, claims=None):
        from tools.instagram import connector as c
        self.events = []
        response = Mock(status_code=200)
        response.json.return_value = {'status': 'ok', 'items': [
            {'pk': i, 'code': f'post{i}', 'media_type': 2,
             'image_versions2': {'candidates': [{'url': f'https://s.cdninstagram.com/{i}', 'width': 320, 'height': 240}]},
             'video_versions': [{'url': f'https://s.cdninstagram.com/{i}.mp4', 'width': 640}]} for i in range(3)]}
        session = Mock()
        def instagram_get(url, **kwargs):
            if url.endswith('/comments/'):
                comments = Mock(status_code=200)
                comments.json.return_value = {'status': 'ok', 'comments': [{'pk': 100, 'text': 'ID: Artist - Title'}]}
                return comments
            return response
        session.get.side_effect = instagram_get
        api = Mock()
        claim_iter = iter(claims or [])
        def post(endpoint, *args, **kwargs):
            self.events.append(endpoint)
            if endpoint == 'sync/': return {'media': [], **sync}
            if endpoint == 'claim/': return next(claim_iter, None)
            if endpoint == 'thumbnails/': return {'results': [{'post_id': '0', 'status': 'available'}]}
        api.post.side_effect = post
        def download(url, dest, **kwargs):
            self.assertTrue(kwargs['image_only'])  # A thumbnail run never downloads videos.
            self.events.append('image')
            if download_effect: raise download_effect
            dest.write_bytes(ThumbnailTests.image())
        args = SimpleNamespace(api='https://example.test', profile='Profile 1', fixture=None, work_seconds=0)
        with patch.dict('os.environ', {'DRAIN_TOKEN': 'synthetic-only'}), \
             patch('tools.instagram.connector.API', return_value=api), \
             patch('tools.instagram.connector.instagram_session', return_value=session), \
             patch('tools.instagram.connector.download', side_effect=download), \
             patch('tools.instagram.connector.ffmpeg_path') as ffmpeg:
            c.run(args)
            ffmpeg.assert_not_called()
        return api

    def test_real_parser_to_jpeg_batch_keeps_claims_and_heartbeat_between_images(self):
        import base64
        from io import BytesIO
        from PIL import Image
        api = self.run_connector({'sync_token': 'token', 'thumbnail_posts': [
            {'post_id': str(i), 'available': False} for i in range(3)]})
        self.assertEqual(self.events[:3], ['sync/', 'heartbeat/', 'heartbeat/'])
        image_positions = [i for i, event in enumerate(self.events) if event == 'image']
        for start, end in zip(image_positions, image_positions[1:]):
            self.assertIn('heartbeat/', self.events[start+1:end])
            self.assertIn('claim/', self.events[start+1:end])
        payload = next(x.args[1] for x in api.post.call_args_list if x.args[0] == 'thumbnails/')
        self.assertEqual([p['post_id'] for p in payload['thumbnails']], ['0', '1', '2'])
        for item in payload['thumbnails']:
            with Image.open(BytesIO(base64.b64decode(item['jpeg']))) as image:
                self.assertEqual(image.size, (320, 240))
                self.assertEqual(image.format, 'JPEG')

    def test_live_shape_legacy_contract_does_no_image_work(self):
        self.run_connector({})
        self.assertNotIn('image', self.events)
        self.assertNotIn('thumbnails/', self.events)

    def test_pending_user_job_completes_before_first_thumbnail(self):
        self.run_connector({'sync_token': 'token', 'thumbnail_posts': [
            {'post_id': '0', 'available': False}]}, claims=[
                {'id': 7, 'instagram_id': '0', 'post_instagram_id': '0', 'position': 0, 'token': 'lease'}])
        self.assertLess(self.events.index('media/7/complete/'), self.events.index('image'))

    def test_thumbnail_restriction_stops_run_and_reports_status(self):
        with self.assertRaisesRegex(ConnectorError, '^rate-limited$'):
            self.run_connector({'sync_token': 'token', 'thumbnail_posts': [
                {'post_id': str(i), 'available': False} for i in range(3)]}, ConnectorError('rate-limited'))
        self.assertEqual(self.events.count('image'), 1)
        self.assertEqual(self.events[-1], 'heartbeat/')
        self.assertNotIn('thumbnails/', self.events)


if __name__ == '__main__': unittest.main()
