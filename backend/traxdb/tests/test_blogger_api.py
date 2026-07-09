"""Token-refresh + posts-pagination tests for the Blogger API fetch path
(all HTTP mocked)."""
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from traxdb.services import blogger_api
from traxdb.services.blogger_api import BloggerAuthError, _mint_access_token, iter_blog_links

_CONFIG = {
    'BLOGGER_CLIENT_ID': 'cid',
    'BLOGGER_CLIENT_SECRET': 'secret',
    'BLOGGER_REFRESH_TOKEN': 'refresh',
}


def _fake_config(key, *a, **k):
    return _CONFIG.get(key, '')


def _resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.text = str(payload)
    return m


class MintAccessTokenTestCase(SimpleTestCase):
    @patch.object(blogger_api, 'get_config', side_effect=_fake_config)
    @patch.object(blogger_api.requests, 'post')
    def test_success_returns_access_token(self, mock_post, _cfg):
        mock_post.return_value = _resp(200, {'access_token': 'ya29.token', 'expires_in': 3599})

        self.assertEqual(_mint_access_token(), 'ya29.token')

    @patch.object(blogger_api, 'get_config', side_effect=_fake_config)
    @patch.object(blogger_api.requests, 'post')
    def test_invalid_grant_raises_rebootstrap_error(self, mock_post, _cfg):
        mock_post.return_value = _resp(400, {'error': 'invalid_grant'})

        with self.assertRaises(BloggerAuthError) as ctx:
            _mint_access_token()
        self.assertIn('blogger_oauth_bootstrap.py', str(ctx.exception))

    @patch.object(blogger_api, 'get_config', side_effect=lambda k, *a, **kw: '')
    @patch.object(blogger_api.requests, 'post')
    def test_missing_config_raises_before_http(self, mock_post, _cfg):
        with self.assertRaises(BloggerAuthError):
            _mint_access_token()
        mock_post.assert_not_called()


START = 'https://traxdb2.blogspot.com'

_BYURL = {'id': '42', 'name': 'TraxDB²', 'posts': {'totalItems': 800}}


def _post_item(list_id, published, url=None):
    return {
        'id': list_id,
        'url': url or f'{START}/2026/07/{list_id}.html',
        'published': published,
        'content': f'<div>MIRROR1: https://pixeldrain.com/l/{list_id}</div>',
    }


@patch.object(blogger_api, '_mint_access_token', return_value='tok')
@patch.object(blogger_api.time, 'sleep')  # keep backoff tests instant
class IterBlogLinksTestCase(SimpleTestCase):
    def test_paginates_with_next_page_token(self, _sleep, _mint):
        pages = [
            _resp(200, _BYURL),
            _resp(200, {
                'items': [_post_item('aaaa1111', '2026-07-05T10:00:00-03:00')],
                'nextPageToken': 'p2',
            }),
            _resp(200, {
                'items': [_post_item('bbbb2222', '2026-06-20T10:00:00-03:00')],
            }),
        ]
        with patch.object(blogger_api.requests, 'get', side_effect=pages) as mock_get:
            links = iter_blog_links(START, max_pages=10)

        self.assertEqual(len(links), 2)
        self.assertEqual(links._pages_scraped, 2)
        by_id = {l.list_id: l for l in links}
        self.assertEqual(by_id['aaaa1111'].inferred_date, '2026-07-05')
        self.assertEqual(by_id['bbbb2222'].inferred_date, '2026-06-20')
        # source_url is the individual post URL, not the start URL
        self.assertIn('/aaaa1111.html', by_id['aaaa1111'].source_url)

        # 1 byurl call + 2 posts.list pages; second page carries the token and
        # every posts.list call requests explicit newest-first ordering.
        self.assertEqual(mock_get.call_count, 3)
        page1_params = mock_get.call_args_list[1].kwargs['params']
        page2_params = mock_get.call_args_list[2].kwargs['params']
        self.assertEqual(page1_params['orderBy'], 'published')
        self.assertNotIn('pageToken', page1_params)
        self.assertEqual(page2_params['pageToken'], 'p2')

    def test_stop_at_or_before_date_stops_pagination(self, _sleep, _mint):
        pages = [
            _resp(200, _BYURL),
            _resp(200, {
                'items': [
                    _post_item('newer001', '2026-07-05T10:00:00-03:00'),
                    _post_item('older001', '2026-06-01T10:00:00-03:00'),
                ],
                'nextPageToken': 'p2',  # must never be followed
            }),
        ]
        with patch.object(blogger_api.requests, 'get', side_effect=pages) as mock_get:
            links = iter_blog_links(START, max_pages=10, stop_at_or_before_date='2026-06-15')

        # Cutoff hit on the older post: no second page fetched.
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(links._pages_scraped, 1)
        # Both posts on the page were still parsed (cutoff stops paging, the
        # date filter downstream handles exclusion).
        self.assertEqual({l.list_id for l in links}, {'newer001', 'older001'})

    def test_retries_on_429_then_succeeds(self, mock_sleep, _mint):
        pages = [
            _resp(429, {'error': 'rate limited'}),
            _resp(200, _BYURL),
            _resp(200, {'items': [_post_item('cccc3333', '2026-07-01T00:00:00-03:00')]}),
        ]
        with patch.object(blogger_api.requests, 'get', side_effect=pages) as mock_get:
            links = iter_blog_links(START, max_pages=10)

        self.assertEqual(len(links), 1)
        self.assertEqual(mock_get.call_count, 3)
        mock_sleep.assert_called_once_with(2)

    def test_retries_on_network_error_then_succeeds(self, mock_sleep, _mint):
        pages = [
            requests.ConnectionError('reset by peer'),
            _resp(200, _BYURL),
            _resp(200, {'items': [_post_item('dddd4444', '2026-07-01T00:00:00-03:00')]}),
        ]
        with patch.object(blogger_api.requests, 'get', side_effect=pages) as mock_get:
            links = iter_blog_links(START, max_pages=10)

        self.assertEqual(len(links), 1)
        self.assertEqual(mock_get.call_count, 3)
        mock_sleep.assert_called_once_with(2)
