"""Browser fixtures only: no Django/API, real library, SSH or external network."""
import json
import os
import subprocess
import sys
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:5189'
MP3 = subprocess.check_output([os.getenv('TEST_FFMPEG', 'ffmpeg'), '-v', 'error', '-f', 'lavfi',
    '-i', 'sine=frequency=440:duration=2', '-f', 'mp3', '-c:a', 'libmp3lame', 'pipe:1'])
REVEAL = 'ocdj-reveal://track/12345678-1234-5678-9abc-123456789abc/' + 'a' * 64

with sync_playwright() as playwright:
    engines = ['chromium', 'webkit'] if os.getenv('CI') else ['chromium']
    for engine in engines:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(15000)
        errors, prepares = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        mode = ['pending']

        def route(r):
            path = urlparse(r.request.url).path.removeprefix('/api/')
            data = dict(results=[], count=0)
            if path == 'library/tracks/':
                data = dict(count=1, results=[dict(id=1, artist='Fixture artist', title='Mac tone',
                    source='mac_snapshot', format='wav', file_path='', filename='',
                    duration_seconds=2, file_size_bytes=32044)])
            elif path == 'library/match/':
                data = dict(status='matched', coverage='indexed_only', matches=[dict(
                    id=1, format='wav', bitrate=128, sample_rate=8000, reveal_url=REVEAL, playback_id=1)])
            elif path == 'library/tracks/1/playback/':
                prepares.append(r.request.method)
                data = dict(status=mode[0])
                if mode[0] == 'ready':
                    data['url'] = '/api/library/tracks/1/audio/'
            elif path == 'library/tracks/1/audio/':
                # Serve synthetic MP3 through the same-origin browser media path.
                r.fulfill(status=200, content_type='audio/mpeg', body=MP3,
                          headers={'Cache-Control': 'private, no-store', 'Accept-Ranges': 'bytes'})
                return
            elif path == 'library/stats/':
                data = dict(total_tracks=1, total_size_bytes=32044, formats={'wav': 1})
            r.fulfill(status=200, content_type='application/json', body=json.dumps(data))

        page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(BASE + '/') else r.abort())
        page.route(BASE + '/api/**', route)
        page.goto(BASE + '/library')
        player = page.get_by_role('region', name='Persistent player')
        expect(page.get_by_role('link', name='Open in Finder')).to_have_attribute('href', REVEAL)
        page.get_by_role('button', name='Play library Fixture artist — Mac tone', exact=True).click()
        expect(player.get_by_text('Waiting for Mac audio… The Mac connector must be running.')).to_be_visible()
        page.get_by_role('link', name='Settings', exact=True).click()
        expect(player.get_by_text('Fixture artist — Mac tone', exact=True)).to_be_visible()
        mode[0] = 'offline'
        expect(player.get_by_role('alert')).to_contain_text('Mac audio unavailable')
        expect(player.get_by_text('Waiting for Mac audio… The Mac connector must be running.')).to_have_count(0)
        expect(player.get_by_role('button', name='Play playback')).to_be_visible()
        mode[0] = 'ready'
        player.get_by_role('button', name='Play playback').click()
        page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
        player.get_by_role('button', name='Pause playback').click()
        page.wait_for_function("document.querySelector('.bottom-player audio').paused")
        page.get_by_role('link', name='Library', exact=True).click()
        before = len(prepares)
        page.get_by_role('button', name='Queue library Fixture artist — Mac tone', exact=True).click()
        expect(player.get_by_text('Queue (2)', exact=True)).to_be_visible()
        assert len(prepares) == before, 'Queue must not request another source'
        player.get_by_role('button', name='Next track').click()
        page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
        player.get_by_role('button', name='Previous track').click()
        page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
        page.get_by_role('link', name='Wanted', exact=True).click()
        expect(player.get_by_text('Queue (2)', exact=True)).to_be_visible()
        assert not errors, errors
        browser.close()
        print(f'PASS {engine}: Library normal entry, Finder, pending/offline/retry, real MP3 decode, pause, queue, next/previous, route continuity; synthetic API only')
