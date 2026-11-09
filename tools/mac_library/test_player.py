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
        match_status = ['matched']
        preview_requests = []
        library_preview = [False]

        def route(r):
            path = urlparse(r.request.url).path.removeprefix('/api/')
            data = dict(results=[], count=0)
            if path == 'library/tracks/':
                data = dict(count=1, results=[dict(id=1, artist='Fixture artist', title='Mac tone',
                    source='mac_snapshot', format='wav', file_path='', filename='', playback_id=1,
                    duration_seconds=2, file_size_bytes=32044,
                    preview_url='/api/fixture.mp3' if library_preview[0] else '')])
            elif path == 'library/match/':
                data = dict(status=match_status[0], coverage='indexed_only', matches=[dict(
                    id=1, format='wav', bitrate=128, sample_rate=8000, reveal_url=REVEAL, playback_id=1)])
            elif path == 'wanted/items/':
                data = dict(count=2, results=[dict(id=i, artist='Fixture artist', title=title,
                    status='pending', preview_checked=checked, added='2026-01-01T00:00:00Z')
                    for i, title, checked in [(1, 'Wanted preview', None), (2, 'Wanted checked', True)]])
            elif path == 'wanted/items/1/preview/':
                preview_requests.append(path)
                data = dict(url='/api/fixture.mp3')
            elif path in ('recognize/jobs/', 'recognize/jobs/1/'):
                job = dict(id=1, status='completed', title='Fixture mix',
                    url='https://youtube.com/watch?v=fixture', engine='shazam',
                    tracklist=[dict(artist='Fixture artist', title='Mix segment', timestamp_start=12,
                        timestamp_end=20, confidence='high')], description_tracks=[])
                data = dict(results=[job]) if path == 'recognize/jobs/' else job
            elif path == 'fixture.mp3':
                r.fulfill(status=200, content_type='audio/mpeg', body=MP3)
                return
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
        page.add_init_script("""
          window.adapterCalls = [];
          window.YT = { Player: function(host, config) {
            window.adapterCalls.push(['segment', config.playerVars.start, config.playerVars.end]);
            this.playVideo = () => { window.adapterCalls.push(['play']); config.events.onStateChange({data: 1}) };
            this.pauseVideo = () => config.events.onStateChange({data: 2});
            this.destroy = () => {};
            setTimeout(() => config.events.onReady(), 0);
          }};
        """)
        def visit(path):
            # Each scenario reloads to reset the player/query cache. Let WebKit
            # finish mocked fetches before destroying the prior document.
            page.wait_for_load_state('networkidle')
            page.goto(BASE + path)
            page.wait_for_load_state('networkidle')

        visit('/library')
        player = page.get_by_role('region', name='Persistent player')
        expect(page.get_by_role('link', name='Open in Finder')).to_have_attribute('href', REVEAL)
        page.get_by_role('button', name='Play Fixture artist — Mac tone', exact=True).click()
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
        page.get_by_role('button', name='Queue Fixture artist — Mac tone', exact=True).click()
        expect(player.get_by_text('Queue (2)', exact=True)).to_be_visible()
        assert len(prepares) == before, 'Queue must not request another source'
        player.get_by_role('button', name='Next track').click()
        page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
        player.get_by_role('button', name='Previous track').click()
        page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
        page.get_by_role('link', name='Wanted', exact=True).click()
        page.wait_for_load_state('networkidle')
        expect(player.get_by_text('Queue (2)', exact=True)).to_be_visible()
        # Shared controls have exactly one pair of actions; Finder is always last.
        def controls(label):
            play = page.get_by_role('button', name='Play ' + label, exact=True)
            expect(play).to_have_count(1)
            expect(page.get_by_role('button', name='Queue ' + label, exact=True)).to_have_count(1)
            expect(page.get_by_role('button', name='Play library ' + label, exact=True)).to_have_count(0)
            return play

        visit('/wanted')
        play = controls('Fixture artist — Wanted checked')
        expect(play).to_be_visible()  # Mac available even when catalog lookup found nothing.
        finder = page.locator('tr').filter(has=play).get_by_role('link', name='Open in Finder')
        expect(finder).to_have_attribute('href', REVEAL)
        assert finder.evaluate('e => e.closest("td") === e.closest("tr").lastElementChild')
        before = len(prepares)
        page.get_by_role('button', name='Queue Fixture artist — Wanted preview', exact=True).click()
        expect(player.get_by_text('Queue (1)', exact=True)).to_be_visible()
        # The first queued item is selected by the existing player; future items remain lazy.
        expect(player.get_by_role('button', name='Play playback')).to_be_visible()
        page.wait_for_function("document.querySelector('.bottom-player audio') !== null")
        before = len(prepares)
        page.get_by_role('button', name='Queue Fixture artist — Wanted preview', exact=True).click()
        expect(player.get_by_text('Queue (2)', exact=True)).to_be_visible()
        assert len(prepares) == before and not preview_requests
        mode[0] = 'offline'
        controls('Fixture artist — Wanted preview').click()
        expect(player.get_by_role('status')).to_have_text('Using fallback · Catalog preview')
        page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
        assert len(preview_requests) == 1
        expect(player.get_by_role('button', name='In Wanted')).to_be_disabled()

        # No exact Mac match keeps normal Wanted preview behavior and never prepares.
        match_status[0] = 'no_indexed_match'
        visit('/wanted')
        expect(page.get_by_text('No catalog preview found.')).to_be_visible()
        before = len(prepares)
        controls('Fixture artist — Wanted preview').click()
        page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
        assert len(prepares) == before

        # Recognize exact match -> Mac offline -> original mix with original bounds.
        match_status[0] = 'matched'
        visit('/recognize')
        play = controls('Fixture artist — Mix segment')
        expect(page.get_by_role('link', name='Open in Finder')).to_be_visible()
        assert play.evaluate('e => e.closest(".track-controls").lastElementChild.matches("a.track-library-match")')
        play.click()
        expect(player.get_by_role('status')).to_have_text('Using fallback · Original mix · identified segment')
        page.wait_for_function("window.adapterCalls.some(c => c[0] === 'play')")
        assert page.evaluate("window.adapterCalls.some(c => c[0] === 'segment' && c[1] === 12 && c[2] === 20)")
        # Ambiguous/absent Mac matches retain normal Recognize playback.
        for status in ('no_indexed_match', 'multiple_matches'):
            match_status[0] = status
            visit('/recognize')
            before = len(prepares)
            controls('Fixture artist — Mix segment').click()
            page.wait_for_function("window.adapterCalls.some(c => c[0] === 'play')")
            assert len(prepares) == before
            expect(player.get_by_role('button', name='Use preview / mix')).to_have_count(0)

        # Shared direct preview can be selected in the persistent player while
        # Mac is pending; this cancels polling without another Mac write.
        match_status[0] = 'matched'
        library_preview[0] = True
        mode[0] = 'pending'
        visit('/library')
        before = len(prepares)
        controls('Fixture artist — Mac tone').click()
        expect(player.get_by_text('Waiting for Mac audio… The Mac connector must be running.')).to_be_visible()
        player.get_by_role('button', name='Use preview / mix').click()
        expect(player.get_by_role('status')).to_have_text('Using fallback · Library audio')
        page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
        page.wait_for_timeout(2100)
        assert len(prepares) == before + 1, f'Fallback must cancel Mac polling without resending: {prepares[before:]}'
        assert not errors, errors
        browser.close()
        print(f'PASS {engine}: Library normal entry, Finder, pending/offline/retry, real MP3 decode, pause, queue, next/previous, route continuity, Wanted lazy fallback, Recognize mix bounds, absent/ambiguous matches, persistent fallback choice; synthetic API only')
