"""Built app + real synthetic Django list/stream, only in CI PostgreSQL."""
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import skipUnless

from django.test import LiveServerTestCase
from django.utils import timezone

from library.models import LibraryTrack
from organize.models import PipelineItem
from soulseek.models import Download
from .test_playback import PlaybackFixture


@skipUnless(os.getenv('ORGANIZE_BROWSER_TEST'), 'CI browser fixture only')
class OrganizeBrowserTests(PlaybackFixture, LiveServerTestCase):
    def test_normal_organize_play_queue_navigation_seek(self):
        from playwright.sync_api import expect, sync_playwright

        second = PipelineItem.objects.create(original_filename='second.wav',
            current_path=str(self.path), artist='Fixture artist', title='Second tone', stage='ready')
        PipelineItem.objects.create(original_filename='missing.wav',
            current_path=str(self.downloads / 'missing.wav'), title='Missing tone')
        archived_item = PipelineItem.objects.create(original_filename='archived.aiff', artist='Archived credit',
            title='Archived tone', stage='published', archive_state='archived',
            archived_at=timezone.now(), music_persistent_id='fixture', sha256='a' * 64)
        LibraryTrack.objects.create(source='mac_snapshot', archive_item_id=archived_item.pk,
            artist='Retagged artist', title='Retagged title', format='aiff',
            file_path='mac-snapshot:00000000-0000-0000-0000-000000000001:' + 'b' * 64)
        download = Download.objects.create(username='fixture', filename='second.wav', status='completed')
        second.download = download
        second.save(update_fields=['download'])
        dist = Path(__file__).resolve().parents[3] / 'dist'
        self.assertTrue((dist / 'index.html').is_file())
        from unittest.mock import patch
        with patch('soulseek.views.SlskdClient') as slskd, sync_playwright() as pw:
            slskd.return_value.get_downloads.return_value = []
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
                        elif path == '/api/organize/release-candidates/':
                            request.fulfill(json={'releases': [{'album': 'Alternate release', 'label': 'Fixture label',
                                'catalog_number': 'CAT-2', 'year': '2021', 'track_number': '2', 'provider': 'Discogs',
                                'url': 'https://www.discogs.com/release/123'}]})
                        elif path.startswith('/api/organize/') or path == '/api/soulseek/downloads/':
                            if request.request.method not in ('GET', 'HEAD'):
                                writes.append(path)
                            if path.endswith('/audio/'):
                                media_requests.append(path)
                            request.continue_()
                        elif path.startswith('/api/'):
                            # An exact indexed match must not replace this row's bytes.
                            data = dict(results=[], count=0)
                            if path == '/api/library/match/' and parse_qs(urlparse(request.request.url).query).get('artist') == ['Fixture artist']:
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
                    expect(page.get_by_text('On Mac', exact=True)).to_be_visible()
                    archived = page.get_by_role('button', name='Queue Archived credit — Archived tone', exact=True)
                    expect(archived).to_be_enabled()
                    archived.click()
                    expect(player.get_by_text('Queue (3)', exact=True)).to_be_visible()
                    # Organize stays within the content width at both sizes.
                    for width in (1200, 390):
                        page.set_viewport_size({'width': width, 'height': 900})
                        self.assertTrue(page.locator('.pipeline-table').evaluate("""table =>
                            table.scrollWidth === table.clientWidth && [...table.querySelectorAll('button')].every(btn =>
                              btn.getBoundingClientRect().right <= table.getBoundingClientRect().right)"""))
                    page.set_viewport_size({'width': 1200, 'height': 900})
                    first = page.locator('.pipeline-table__row').filter(has=page.get_by_role(
                        'button', name='Play Fixture artist — Pipeline tone', exact=True))
                    first.get_by_role('button', name='Edit tags').click()
                    page.get_by_role('button', name='Find releases').click()
                    page.get_by_role('button', name='Use release').click()
                    expect(page.get_by_label('Album', exact=True)).to_have_value('Alternate release')
                    expect(page.get_by_label('Title', exact=True)).to_have_value('Pipeline tone')
                    expect(page.get_by_label('Catalog #', exact=True)).to_have_value('CAT-2')
                    page.get_by_role('button', name='Cancel', exact=True).click()
                    page.get_by_role('link', name='Soulseek', exact=True).click()
                    row = page.locator('.dl-row--completed')
                    expect(row.get_by_text('Organize · VPS', exact=True)).to_be_visible()
                    row.get_by_role('button', name='Play Fixture artist — Second tone', exact=True).click()
                    page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
                    expect(row.get_by_text('Playing', exact=True)).to_be_visible()
                    expect(player.locator('audio')).to_have_attribute('src', f'/api/organize/pipeline/{second.pk}/audio/')
                    self.assertFalse(writes)
                    self.assertFalse(errors)
                    browser.close()
