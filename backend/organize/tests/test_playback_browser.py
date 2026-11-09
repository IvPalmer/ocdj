"""Built app + real synthetic Django list/stream, only in CI PostgreSQL."""
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from unittest import skipUnless

from django.test import LiveServerTestCase
from django.utils import timezone

from organize.models import PipelineItem
from .test_playback import PlaybackFixture


@skipUnless(os.getenv('ORGANIZE_BROWSER_TEST'), 'CI browser fixture only')
class OrganizeBrowserTests(PlaybackFixture, LiveServerTestCase):
    def test_normal_organize_play_queue_navigation_seek(self):
        from playwright.sync_api import expect, sync_playwright

        second = PipelineItem.objects.create(original_filename='second.wav',
            current_path=str(self.path), artist='Fixture artist', title='Second tone', stage='ready')
        PipelineItem.objects.create(original_filename='missing.wav',
            current_path=str(self.downloads / 'missing.wav'), title='Missing tone')
        PipelineItem.objects.create(original_filename='archived.aiff', artist='Fixture artist',
            title='Archived tone', stage='published', archive_state='archived',
            archived_at=timezone.now(), music_persistent_id='fixture', sha256='a' * 64)
        dist = Path(__file__).resolve().parents[3] / 'dist'
        self.assertTrue((dist / 'index.html').is_file())
        with sync_playwright() as pw:
            for engine in ['chromium', 'webkit']:
                with self.subTest(engine=engine):
                    browser = getattr(pw, engine).launch()
                    page = browser.new_page()
                    errors, writes, media_requests = [], [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))

                    def route(request):
                        path = urlparse(request.request.url).path
                        if not request.request.url.startswith(self.live_server_url + '/'):
                            request.abort()
                        elif path.startswith('/api/organize/'):
                            if request.request.method not in ('GET', 'HEAD'):
                                writes.append(path)
                            if path.endswith('/audio/'):
                                media_requests.append(path)
                            request.continue_()
                        elif path.startswith('/api/'):
                            # An exact indexed match must not replace this row's bytes.
                            data = dict(results=[], count=0)
                            if path == '/api/library/match/':
                                data = dict(status='matched', matches=[dict(playback_id=999, format='mp3')])
                            request.fulfill(content_type='application/json', body=json.dumps(data))
                        else:
                            asset = dist / path.lstrip('/') if path.startswith('/assets/') else dist / 'index.html'
                            request.fulfill(path=str(asset))
                    page.route('**/*', route)
                    page.goto(self.live_server_url + '/organize')
                    player = page.get_by_role('region', name='Persistent player')
                    expect(page.get_by_text('File unavailable on server', exact=True)).to_be_visible()
                    expect(page.get_by_role('button', name='Play Missing tone', exact=True)).to_have_count(0)
                    page.get_by_role('button', name='Play Fixture artist — Pipeline tone', exact=True).click()
                    page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
                    expect(player.get_by_role('button', name='In Wanted')).to_have_count(0)
                    before = len(media_requests)
                    page.get_by_role('button', name='Queue Fixture artist — Second tone', exact=True).click()
                    expect(player.get_by_text('Queue (2)', exact=True)).to_be_visible()
                    self.assertEqual(len(media_requests), before)
                    page.get_by_role('link', name='Settings', exact=True).click()
                    expect(player.get_by_text('Fixture artist — Pipeline tone', exact=True)).to_be_visible()
                    page.evaluate("document.querySelector('.bottom-player audio').currentTime = 12")
                    page.wait_for_function("document.querySelector('.bottom-player audio').currentTime >= 12")
                    player.get_by_role('button', name='Pause playback').click()
                    page.wait_for_function("document.querySelector('.bottom-player audio').paused")
                    player.get_by_role('button', name='Next track').click()
                    page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
                    expect(player.locator('audio')).to_have_attribute('src', f'/api/organize/pipeline/{second.pk}/audio/')
                    player.get_by_role('button', name='Previous track').click()
                    page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
                    expect(player.locator('audio')).to_have_attribute('src', self.url)
                    # Real browser request against the actual Range endpoint.
                    result = page.evaluate("""async url => {
                        const response = await fetch(url, {headers: {Range: 'bytes=100-199'}});
                        return [response.status, response.headers.get('Content-Range'), (await response.arrayBuffer()).byteLength];
                    }""", self.url)
                    self.assertEqual(result, [206, f'bytes 100-199/{self.path.stat().st_size}', 100])
                    page.get_by_role('link', name='Organize', exact=True).click()
                    page.get_by_role('button', name='Show archived (1)', exact=True).click()
                    archived = page.get_by_role('button', name='Queue Fixture artist — Archived tone', exact=True)
                    expect(archived).to_be_enabled()
                    archived.click()
                    expect(player.get_by_text('Queue (3)', exact=True)).to_be_visible()
                    self.assertFalse(writes)
                    self.assertFalse(errors)
                    browser.close()
