"""Chromium contract test with synthetic API responses and built static assets.
Run on CI/VPS: python -m unittest tools.soulseek.test_clear_browser -v
"""
import copy
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


class ClearBrowserTests(unittest.TestCase):
    def test_buttons_counts_modes_refetch_reload_and_partial_retry(self):
        rows = [{'id': i, 'filename': f'fixture-{i}.flac', 'username': 'fixture-peer',
                 'status': status, 'progress': 0}
                for i, status in enumerate(['completed', 'failed', 'cancelled', 'queued', 'downloading'])]
        modes = []
        fetches = []
        fail_next = [False]
        removed = []

        def api(route):
            nonlocal rows
            path = route.request.url.split('/api/', 1)[1].split('?', 1)[0]
            body, status = {}, 200
            if path == 'soulseek/downloads/clear/':
                mode = route.request.post_data_json['mode']
                modes.append(mode)
                selected = ['completed'] if mode == 'completed' else ['failed', 'cancelled']
                if fail_next[0]:
                    # One cancelled row cleared, failed row remains for retry.
                    rows = [d for d in rows if d['status'] != 'cancelled']
                    body = {'error': 'Some slskd history could not be cleared. Remaining entries were preserved; try again.',
                            'cleared': 1, 'slskd_cleared': 1, 'slskd_failed': 1}
                    status = 502
                    fail_next[0] = False
                else:
                    rows = [d for d in rows if d['status'] not in selected]
                    body = {'cleared': 1, 'slskd_cleared': 1, 'slskd_failed': 0}
            elif path == 'soulseek/downloads/remove/':
                assert route.request.method == 'DELETE'
                target = route.request.post_data_json['download_id']
                removed.append(target)
                rows = [d for d in rows if d['id'] != target]
                route.fulfill(status=204)
                return
            elif path == 'soulseek/downloads/':
                body = {'downloads': copy.deepcopy(rows), 'download_indicators': {}}
                fetches.append(len(rows))
            elif path == 'soulseek/health/':
                body = {'status': 'connected', 'info': {'server': {'isLoggedIn': True}}}
            elif path == 'soulseek/queue/':
                body = {'results': [{'id': 9, 'raw_query': 'fixture search', 'status': 'found',
                                     'last_searched': '2026-01-01T00:00:00Z', 'search_count': 1}]}
            elif path == 'soulseek/search/results/':
                body = [
                    {'id': 1, 'filename': 'available.flac', 'username': 'same-peer', 'is_locked': False},
                    {'id': 2, 'filename': 'private.flac', 'username': 'same-peer', 'is_locked': True},
                ]
            route.fulfill(status=status, content_type='application/json', body=json.dumps(body))

        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT / 'dist'))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=['--no-sandbox'])
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('**/api/**', api)
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.get_by_role('link', name='Soulseek', exact=True).click()
                expect(page.get_by_role('button', name='Clear Completed (1)', exact=True)).to_be_visible()
                expect(page.get_by_role('button', name='Clear Failed (2)', exact=True)).to_be_visible()
                page.get_by_role('button', name='Clear Completed (1)', exact=True).click()
                expect(page.get_by_role('button', name='Clear Completed', exact=False)).to_have_count(0)
                expect(page.locator('.dl-row')).to_have_count(4)
                # Reload the built app via / (the static fixture has no SPA fallback).
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.get_by_role('link', name='Soulseek', exact=True).click()
                expect(page.get_by_role('button', name='Clear Completed', exact=False)).to_have_count(0)
                expect(page.get_by_role('button', name='Clear Failed (2)', exact=True)).to_be_visible()
                fail_next[0] = True
                page.get_by_role('button', name='Clear Failed (2)', exact=True).click()
                expect(page.get_by_role('alert')).to_contain_text('Remaining entries were preserved')
                expect(page.get_by_role('button', name='Clear Failed (1)', exact=True)).to_be_visible()
                page.get_by_role('button', name='Clear Failed (1)', exact=True).click()
                expect(page.get_by_role('button', name='Clear Failed', exact=False)).to_have_count(0)
                expect(page.get_by_role('alert')).to_have_count(0)
                expect(page.locator('.dl-row')).to_have_count(2)
                expect(page.get_by_text('fixture-3.flac', exact=True)).to_be_visible()
                expect(page.get_by_text('fixture-4.flac', exact=True)).to_be_visible()
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.get_by_role('link', name='Soulseek', exact=True).click()
                expect(page.locator('.dl-row')).to_have_count(2)
                self.assertEqual(modes, ['completed', 'failed', 'failed'])
                self.assertIn(3, fetches)  # Error response still caused a refetch.
                rows.append({'id': 'slskd:peer:timeout', 'filename': 'timeout.flac',
                             'status': 'failed', 'slskd_state': 'Completed, TimedOut', 'untracked': True})
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.get_by_role('link', name='Soulseek', exact=True).click()
                timeout_row = page.locator('.dl-row').filter(has_text='timeout.flac')
                timeout_row.get_by_title('Remove from list').click()
                expect(timeout_row).to_have_count(0)
                expect(page.locator('.dl-row')).to_have_count(2)
                self.assertEqual(removed, ['slskd:peer:timeout'])
                page.get_by_text('fixture search', exact=True).click()
                expect(page.locator('.er-file').filter(has_text='private.flac').get_by_text('Locked', exact=True)).to_be_visible()
                expect(page.locator('.er-file').filter(has_text='available.flac').get_by_text('Locked', exact=True)).to_have_count(0)
                self.assertEqual(errors, [])
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
