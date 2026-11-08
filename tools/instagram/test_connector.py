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


if __name__ == '__main__': unittest.main()
