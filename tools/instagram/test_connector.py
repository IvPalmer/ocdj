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
                job = {'id': 7, 'instagram_id': '202', 'token': 'lease-token'}
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
                    call('media/7/complete/', {'token': 'lease-token', 'status': 'error', 'candidates': []}),
                    call('heartbeat/', {'status': status}),
                ])

    def test_read_stops_at_five_and_ignores_publication_order(self):
        session = Mock(); session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = {'status': 'ok', 'items': [
            {'pk': i, 'code': f'post{i}', 'media_type': 1} for i in range(8)], 'more_available': True, 'next_max_id': 'next'}
        data, _ = read_collection(session)
        self.assertEqual(len(data['posts']), 5); self.assertEqual(session.get.call_count, 1)


if __name__ == '__main__': unittest.main()
