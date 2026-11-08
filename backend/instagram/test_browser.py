"""Optional real Chromium fixture test: pip install -r tools/instagram/qa-requirements.txt.
Runs entirely within Django's temporary PostgreSQL test database and live server.
"""
from concurrent.futures import ThreadPoolExecutor
from django.db import connections
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import requests
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import path, re_path
from django.views.static import serve
from djtools_project.urls import urlpatterns as app_urls

ROOT = Path(__file__).resolve().parents[2]
urlpatterns = app_urls + [path('instagram', serve, {'path': 'index.html', 'document_root': ROOT / 'dist'}),
                          re_path(r'^assets/(?P<path>.*)$', serve, {'document_root': ROOT / 'dist/assets'})]


@skipUnless(os.environ.get('INSTAGRAM_BROWSER_TEST') == '1', 'Opt in to Chromium fixture QA')
@override_settings(ROOT_URLCONF=__name__)
class BrowserFixtureTests(StaticLiveServerTestCase):
    def test_carousel_play_seek_correct_review_wishlist_resync_mobile(self):
        from playwright.sync_api import sync_playwright, expect
        scratch = Path(os.environ['PAPERCLIP_SCRATCH_DIR'])
        with tempfile.TemporaryDirectory(dir=scratch) as owned:
            with override_settings(INSTAGRAM_CACHE_ROOT=Path(owned)/'cache'):
                data = json.loads((ROOT/'tools/instagram/fixture.json').read_text())
                with override_settings(ALLOWED_HOSTS=['*']):
                    with patch.dict(os.environ, {'DRAIN_TOKEN': 'fixture-test-token'}):
                        subprocess.run([sys.executable, str(ROOT/'tools/instagram/connector.py'),
                                        '--api', self.live_server_url, '--fixture', str(ROOT/'tools/instagram/fixture.json')],
                                       check=True, timeout=120)
                        def livepost(endpoint, payload):
                            response = requests.post(self.live_server_url+'/api/instagram/'+endpoint, json=payload,
                                headers={'Authorization': 'Bearer fixture-test-token'}, timeout=10)
                            self.assertEqual(response.status_code, 200, response.text)
                            return response.json()
                        def db_call(fn):
                            # Playwright's sync API runs an event loop on this thread.
                            def run():
                                try:
                                    return fn()
                                finally:
                                    connections.close_all()
                            with ThreadPoolExecutor(max_workers=1) as pool:
                                return pool.submit(run).result()
                        with sync_playwright() as pw:
                            browser = pw.chromium.launch(executable_path=os.environ.get('CHROME'), args=['--no-sandbox'])
                            page = browser.new_page(viewport={'width': 1440, 'height': 1150})
                            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                            page.goto(self.live_server_url+'/instagram')
                            expect(page.get_by_role('heading', name='Track Downloads')).to_be_visible()
                            expect(page.get_by_text('18 videos identified', exact=False)).to_be_visible()
                            page.get_by_role('button', name='Grid', exact=True).click()
                            grid = page.get_by_role('navigation', name='Saved posts')
                            cards = grid.get_by_role('button')
                            expect(cards).to_have_count(5)
                            expect(grid.locator('video')).to_have_count(1)
                            cards.nth(1).click()
                            expect(cards.nth(1)).to_have_attribute('aria-current', 'true')
                            expect(page.locator('.ig-player video')).to_have_attribute('aria-label', 'Post 2, video 1')
                            cards.first.click()
                            page.get_by_role('button', name='List', exact=True).click()
                            expect(cards.first).to_have_attribute('aria-current', 'true')
                            player = page.locator('video'); first_src = player.get_attribute('src')
                            first_version = player.get_attribute('data-playback-version')
                            player.evaluate('(v) => v.play()')
                            page.wait_for_function('document.querySelector("video").currentTime > 0.2')
                            player.evaluate('(v) => { v.pause(); v.currentTime = 1.5 }')
                            page.wait_for_function('document.querySelector("video").currentTime >= 1.4')
                            # Real stream failure and connector reupload for the same selected child.
                            from instagram.models import Media
                            from instagram import cache
                            first = db_call(lambda: Media.objects.get(pk=int(first_src.split('/')[-3])))
                            cached = cache.path(first.cache_key)
                            payload = cached.read_bytes(); cached.unlink()
                            player.evaluate("(v) => { v.src += '?failure=1'; v.load() }")
                            expect(page.get_by_text('Playback unavailable or cache expired.', exact=False)).to_be_visible()
                            page.screenshot(path=str(scratch/'instagram-playback-failure.png'), full_page=True)
                            with page.expect_response(lambda r: r.url.endswith('/playback/') and r.request.method == 'POST') as requested:
                                page.get_by_role('button', name='Retry playback').click()
                            self.assertEqual(requested.value.status, 200)
                            refresh_job = livepost('connector/claim/', {})
                            self.assertEqual(refresh_job['action'], 'playback')
                            self.assertEqual(refresh_job['id'], first.pk)
                            response = requests.post(self.live_server_url+f'/api/instagram/connector/media/{first.pk}/upload/',
                                files={'file': ('fixture.mp4', payload, 'video/mp4')},
                                headers={'Authorization': 'Bearer fixture-test-token',
                                         'X-Instagram-Lease': refresh_job['token']}, timeout=10)
                            self.assertEqual(response.status_code, 200, response.text)
                            livepost(f"connector/media/{first.pk}/complete/", {
                                'token': refresh_job['token'], 'status': 'no-match', 'candidates': []})
                            page.get_by_role('button', name='Refresh status').click()
                            expect(page.locator('video')).not_to_have_attribute('data-playback-version', first_version)
                            expect(page.locator('video')).not_to_have_attribute('src', first_src)
                            page.locator('video').evaluate('(v) => v.play()')
                            page.wait_for_function('document.querySelector("video").currentTime > 0.2')
                            page.locator('video').evaluate('(v) => { v.pause(); v.currentTime = 1.5 }')
                            page.wait_for_function('document.querySelector("video").currentTime >= 1.4')
                            page.screenshot(path=str(scratch/'instagram-playback-recovered.png'), full_page=True)
                            page.get_by_role('button', name='video 2, pending', exact=True).click()
                            expect(page.locator('video')).not_to_have_attribute('src', first_src)
                            self.assertEqual(page.locator('video').count(), 1)
                            page.locator('video').evaluate('(v) => v.play()')
                            page.wait_for_function('document.querySelector("video").currentTime > 0.2')
                            page.locator('video').evaluate('(v) => { v.pause(); v.currentTime = 1.5 }')
                            page.get_by_role('button', name='Recognize').click()
                            expect(page.get_by_text('Recognition queued', exact=False)).to_be_visible()
                            job = livepost('connector/claim/', {})
                            livepost(f"connector/media/{job['id']}/complete/", {
                                'token': job['token'], 'status': 'identified', 'candidates': [{'artist': 'Fixture artist', 'title': 'Synthetic preview tone'}]})
                            page.get_by_role('button', name='Refresh status').click()
                            expect(page.get_by_label('Artist', exact=True)).to_have_value('Fixture artist')
                            audition_path = f"**/media/{job['id']}/candidate-preview/"
                            fixture_audio_url = player.get_attribute('src')
                            page.route(audition_path, lambda route: route.fulfill(json={
                                'url': fixture_audio_url, 'provider': 'itunes', 'track_url': '',
                            }))
                            player.evaluate('(v) => { v.loop = true; return v.play() }')
                            page.get_by_role('button', name='Listen to preview of Fixture artist — Synthetic preview tone').click()
                            audition = page.get_by_label('Recognized track preview')
                            expect(audition).to_be_visible()
                            audition.evaluate('(a) => a.play()')
                            page.wait_for_function('document.querySelector("audio").currentTime > 0')
                            self.assertTrue(player.evaluate('(v) => v.paused'))
                            expect(page.get_by_label('Artist', exact=True)).to_have_value('Fixture artist')
                            expect(page.get_by_role('button', name='Mark reviewed', exact=True)).to_be_visible()
                            expect(page.get_by_role('button', name='Add to wishlist', exact=True)).to_be_enabled()
                            page.unroute(audition_path)
                            page.route(audition_path, lambda route: route.fulfill(json={
                                'url': '', 'provider': '', 'track_url': '',
                            }))
                            page.get_by_role('button', name='Listen to preview of Fixture artist — Synthetic preview tone').click()
                            expect(page.get_by_text('No catalog preview is available for this match.')).to_be_visible()
                            expect(audition).to_have_count(0)
                            page.get_by_label('Artist', exact=True).fill('Corrected fixture artist')
                            page.get_by_label('Track title', exact=True).fill('Corrected fixture title')
                            page.get_by_role('button', name='Save correction').click()
                            expect(page.get_by_role('button', name='Save correction')).to_be_disabled()
                            page.get_by_role('button', name='Mark reviewed', exact=True).click()
                            expect(page.get_by_role('button', name='Mark unreviewed', exact=True)).to_be_visible()
                            page.get_by_role('button', name='Add to wishlist', exact=True).click()
                            expect(page.get_by_role('button', name='Added to wishlist', exact=True)).to_be_disabled()
                            from wanted.models import WantedItem
                            previous = db_call(lambda: WantedItem.objects.get(artist='Corrected fixture artist', title='Corrected fixture title'))
                            page.get_by_label('Artist', exact=True).fill('Corrected track B artist')
                            page.get_by_label('Track title', exact=True).fill('Corrected track B title')
                            page.get_by_role('button', name='Save correction').click()
                            expect(page.get_by_role('button', name='Add to wishlist', exact=True)).to_be_enabled()
                            expect(page.get_by_text('In wishlist', exact=True)).to_have_count(0)
                            expect(page.get_by_role('button', name='Mark unreviewed', exact=True)).to_be_visible()
                            page.screenshot(path=str(scratch/'instagram-corrected-before-wishlist.png'), full_page=True)
                            page.get_by_role('button', name='Add to wishlist', exact=True).click()
                            expect(page.get_by_role('button', name='Added to wishlist', exact=True)).to_be_disabled()
                            current = db_call(lambda: Media.objects.get(artist='Corrected track B artist'))
                            repeated = livepost(f'media/{current.pk}/wishlist/', {})
                            self.assertEqual(repeated['wanted_id'], current.wanted_id)
                            self.assertEqual(db_call(lambda: WantedItem.objects.filter(artist='Corrected track B artist', title='Corrected track B title').count()), 1)
                            db_call(previous.refresh_from_db)
                            self.assertEqual((previous.artist, previous.title), ('Corrected fixture artist', 'Corrected fixture title'))
                            self.assertTrue(current.reviewed)
                            livepost('connector/sync/', data)
                            page.get_by_role('button', name='Refresh status').click()
                            expect(page.get_by_label('Artist', exact=True)).to_have_value('Corrected track B artist')
                            page.screenshot(path=str(scratch/'instagram-desktop.png'), full_page=True)
                            page.set_viewport_size({'width': 390, 'height': 844})
                            expect(page.get_by_label('Artist', exact=True)).to_be_visible()
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                            page.wait_for_function('document.querySelector(".sidebar").getBoundingClientRect().right <= 1')
                            page.get_by_role('button', name='Grid', exact=True).click()
                            expect(cards).to_have_count(5)
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                            page.screenshot(path=str(scratch/'instagram-mobile.png'), full_page=True)
                            self.assertEqual(errors, [])
                            browser.close()
