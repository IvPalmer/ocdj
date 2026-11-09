"""Synthetic browser fixtures for the shared player; no live/private services.
Run against Vite: python3 tools/test_bottom_player.py http://127.0.0.1:5186
Requires the operator's existing Python Playwright installation.
"""
import io
import json
import sys
import wave
from playwright.sync_api import sync_playwright, expect

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:5186'


def audio_bytes():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\0\0' * 8000 * 30)
    return stream.getvalue()


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    writes = []
    media = dict(id=1, kind='video', position=0, status='identified', reviewed=False,
                 artist='Fixture artist', title='Source tone', playback_url='/api/fixture.wav',
                 playback_version='synthetic-1', candidates=[dict(artist='Fixture artist', title='Catalog tone')])
    instagram = dict(fixture=True, connector_status='ready', posts=[dict(
        id=1, author='synthetic', caption='Synthetic playback fixture', permalink='https://example.com/', media=[media])])
    jobs = [dict(id=i, status='completed', title=title, url=url, engine='shazam',
                 tracklist=[dict(artist='Fixture artist', title=title, timestamp_start=12,
                                 timestamp_end=20, confidence='high')], description_tracks=[])
            for i, title, url in [(1, 'YouTube segment', 'https://www.youtube.com/watch?feature=share&v=fixture'),
                                   (2, 'SoundCloud segment', 'https://soundcloud.com/fixture/mix'),
                                   (3, 'Unsupported segment', 'https://mixcloud.com/fixture/mix')]]
    wav = audio_bytes()

    def route(request_route):
        request = request_route.request
        path = request.url.split('/api/', 1)[1].split('?', 1)[0]
        if path == 'fixture.wav':
            request_route.fulfill(status=200, content_type='audio/wav', body=wav)
            return
        if request.method != 'GET':
            writes.append((path, request.post_data_json))
        if path == 'instagram/':
            data = instagram
        elif path.endswith('candidate-preview/'):
            data = dict(url='/api/fixture.wav', provider='itunes')
        elif path == 'recognize/jobs/':
            data = dict(results=jobs)
        elif path.startswith('recognize/jobs/'):
            data = next(job for job in jobs if str(job['id']) == path.split('/')[2])
        elif path == 'library/tracks/':
            data = dict(count=1, results=[dict(id=1, artist='Fixture artist', title='Indexed tone',
                filename='fixture.flac', format='flac', file_path='/synthetic/fixture.flac')])
        elif path == 'wanted/shazam/status/':
            data = dict(total=1, spotify_connected=True, overdue=False)
        elif path == 'wanted/items/2/preview/':
            data = dict(url='/api/fixture.wav', provider='itunes')
        elif path == 'wanted/items/1/online-source/':
            data = dict(url='https://www.youtube.com/watch?v=fixture0001', provider='youtube')
        elif path == 'wanted/items/' and 'identified_via=shazam' in request.url:
            data = dict(count=1, results=[dict(id=2, artist='Fixture artist', title='Shazam tone',
                 status='identified', notes='synthetic fixture', added='2026-01-01T00:00:00Z')])
        elif path == 'wanted/items/' and request.method == 'GET':
            data = dict(count=2, results=[dict(id=1, artist='Fixture artist', title='Wanted tone',
                 status='pending', preview_checked='2026-01-01T00:00:00Z', added='2026-01-01T00:00:00Z'),
                dict(id=3, artist='Fixture artist', title='Spotify tone', status='pending',
                     notes='https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT',
                     added='2026-01-01T00:00:00Z')])
        elif path == 'wanted/items/':
            data = dict(id=7)
        elif path == 'soulseek/queue/add/':
            data = [dict(id=8, status='pending')]
        else:
            data = dict(results=[], count=0)
        request_route.fulfill(status=200, content_type='application/json', body=json.dumps(data))

    page.route('https://**', lambda r: r.abort())
    page.route(BASE + '/api/**', route)
    page.route('https://open.spotify.com/embed/iframe-api/v1', lambda r: r.fulfill(
        content_type='application/javascript', body='''
          window.onSpotifyIframeApiReady({createController(target, options, callback) {
            const handlers = {};
            const update = isPaused => handlers.playback_update?.({data: {
              playingURI: options.uri, isPaused, duration: 30000, position: 0 }});
            window.adapterCalls.push(['spotify-uri', options.uri]);
            callback({addListener: (name, fn) => { handlers[name] = fn },
              resume: () => update(false), pause: () => update(true), destroy: () => target.remove() });
            setTimeout(() => handlers.ready?.(), 0);
          }});
        '''))
    page.route('https://w.soundcloud.com/**', lambda r: r.fulfill(body='<html>Synthetic SoundCloud adapter</html>', content_type='text/html'))
    page.add_init_script('''
      window.adapterCalls = [];
      window.YT = { Player: function(host, config) {
        const frame = document.createElement('iframe'); frame.title = 'Synthetic YouTube source'; host.appendChild(frame);
        window.adapterCalls.push(['youtube-segment', config.videoId, config.playerVars.start, config.playerVars.end]);
        this.playVideo = () => { window.adapterCalls.push(['youtube-play']); config.events.onStateChange({data: 1}) };
        this.pauseVideo = () => { window.adapterCalls.push(['youtube-pause']); config.events.onStateChange({data: 2}) };
        this.destroy = () => { frame.remove() };
        setTimeout(() => config.events.onReady(), 0);
      }};
      window.SC = { Widget: Object.assign(function() {
        const handlers = {};
        return { bind: (name, fn) => { handlers[name] = fn; if (name === 'ready') setTimeout(fn, 0) },
          unbind: name => delete handlers[name], seekTo: value => window.adapterCalls.push(['sc-seek', value]),
          play: () => { window.adapterCalls.push(['sc-play']); handlers.play?.() },
          pause: () => { window.adapterCalls.push(['sc-pause']); handlers.pause?.() } };
      }, { Events: { READY: 'ready', PLAY: 'play', PAUSE: 'pause', FINISH: 'finish', ERROR: 'error', PLAY_PROGRESS: 'progress' } }) };
    ''')
    page.goto(BASE + '/instagram')
    page.wait_for_load_state('networkidle', timeout=30000)
    if errors: raise AssertionError(errors)
    player = page.get_by_role('region', name='Persistent player')
    expect(player.get_by_text('Nothing playing')).to_be_visible()
    page.get_by_role('button', name='Listen to preview of Fixture artist — Catalog tone').click()
    expect(player.get_by_text('Fixture artist — Catalog tone', exact=True)).to_be_visible()
    page.wait_for_function("document.querySelector('.bottom-player audio')?.paused === false")
    page.wait_for_function("document.querySelector('.bottom-player audio')?.currentTime > 0")
    page.get_by_role('button', name='Queue Fixture artist — Source tone', exact=True).click()
    player.get_by_text('Queue (2)', exact=True).click()
    expect(player.get_by_text('Up next: Fixture artist — Source tone')).to_be_visible()
    player.get_by_text('Queue (2)', exact=True).click()
    page.get_by_role('link', name='Library', exact=True).click()
    expect(page.get_by_text('Library file indexed; no browser audio stream is exposed.')).to_be_visible()
    expect(player.get_by_text('Fixture artist — Catalog tone', exact=True)).to_be_visible()
    page.wait_for_function("document.querySelector('.bottom-player audio').paused === false")
    player.get_by_role('button', name='Pause playback').click()
    page.wait_for_function("document.querySelector('.bottom-player audio').paused")
    player.get_by_role('button', name='Play playback').click()
    page.wait_for_function("!document.querySelector('.bottom-player audio').paused")
    player.get_by_role('button', name='Next track').click()
    expect(player.get_by_text('Fixture artist — Source tone', exact=True)).to_be_visible()
    player.get_by_role('button', name='Previous track').click()
    expect(player.get_by_text('Fixture artist — Catalog tone', exact=True)).to_be_visible()
    # Native ended event advances to the queued source.
    page.locator('.bottom-player audio').evaluate("audio => audio.dispatchEvent(new Event('ended'))")
    expect(player.get_by_text('Fixture artist — Source tone', exact=True)).to_be_visible()
    page.locator('.bottom-player audio').evaluate("audio => audio.dispatchEvent(new Event('error'))")
    expect(player.get_by_role('alert')).to_contain_text('Audio source unavailable')
    player.get_by_role('button', name='Play playback').click()
    page.wait_for_function("!document.querySelector('.bottom-player audio').paused")
    player.get_by_role('button', name='Add to Wanted').click()
    expect(player.get_by_role('status')).to_have_text('Added to Wanted')
    player.get_by_role('button', name='Find on Soulseek').click()
    expect(page).to_have_url(BASE + '/soulseek')
    assert ('wanted/items/', dict(artist='Fixture artist', title='Source tone')) in writes
    assert ('soulseek/queue/add/', dict(query='Fixture artist Source tone')) in writes
    assert not any('clear' in path for path, _ in writes)
    page.get_by_role('link', name='Wanted', exact=True).click()
    wanted_row = page.get_by_role('row').filter(has_text='Wanted tone')
    wanted_row.get_by_role('button', name='Play Fixture artist — Wanted tone').click()
    page.wait_for_function("window.adapterCalls.some(c => c[0] === 'youtube-segment' && c[1] === 'fixture0001')")
    expect(wanted_row.get_by_text('Playing', exact=True)).to_be_visible()
    player.get_by_role('button', name='Pause playback').click()
    expect(wanted_row.get_by_text('Paused', exact=True)).to_be_visible()
    assert not any(path == 'wanted/items/1/preview/' for path, _ in writes)
    page.get_by_role('link', name='Shazam', exact=True).click()
    page.get_by_role('button', name='Queue Fixture artist — Shazam tone', exact=True).click()
    assert ('wanted/items/2/preview/', {}) not in writes
    player.get_by_text('Queue (4)', exact=True).click()
    player.get_by_role('button', name='Up next: Fixture artist — Shazam tone').click()
    page.wait_for_function("document.querySelector('.bottom-player audio')?.paused === false")
    assert ('wanted/items/2/preview/', {}) in writes
    expect(page.get_by_text('Playing', exact=True)).to_be_visible()
    expect(player.get_by_role('button', name='In Wanted')).to_be_disabled()
    page.get_by_role('link', name='YouTube', exact=True).click()
    expect(player.get_by_text('Fixture artist — Shazam tone', exact=True)).to_be_visible()
    player.get_by_text('Queue (4)', exact=True).click()
    page.get_by_role('link', name='Instagram', exact=True).click()
    page.locator('video[controls]').evaluate('video => video.play()')
    page.wait_for_function("document.querySelector('.bottom-player audio').paused")
    page.get_by_role('button', name='Play Fixture artist — Source tone', exact=True).click()
    page.wait_for_function("document.querySelector('video[controls]').paused")
    page.set_viewport_size(dict(width=390, height=844))
    expect(player.get_by_role('button', name='Pause playback')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='/tmp/ocdj-bottom-player-mobile.png', full_page=True)
    page.set_viewport_size(dict(width=1280, height=900))
    page.get_by_role('link', name='Recognize', exact=True).click()
    page.get_by_role('button', name='Play Fixture artist — YouTube segment', exact=True).click()
    page.wait_for_function("window.adapterCalls.some(c => c[0] === 'youtube-play')")
    assert page.evaluate("window.adapterCalls.some(c => c[0] === 'youtube-segment' && c[1] === 'fixture' && c[2] === 12 && c[3] === 20)")
    player.get_by_role('button', name='Pause playback').click()
    page.wait_for_function("window.adapterCalls.some(c => c[0] === 'youtube-pause')")
    # History rows are selectable, preserving the existing job navigation.
    page.locator('.recognize-job-card').filter(has_text='SoundCloud segment').click()
    page.get_by_role('button', name='Play Fixture artist — SoundCloud segment', exact=True).click()
    page.wait_for_function("window.adapterCalls.some(c => c[0] === 'sc-play')")
    assert page.evaluate("window.adapterCalls.some(c => c[0] === 'sc-seek' && c[1] === 12000)")
    page.locator('.recognize-job-card').filter(has_text='Unsupported segment').click()
    expect(page.get_by_text('No direct audio source or supported YouTube/SoundCloud mix. Shazam links identify tracks; they are not audio streams.')).to_be_visible()
    expect(player.get_by_text('Fixture artist — SoundCloud segment', exact=True)).to_be_visible()
    page.screenshot(path='/tmp/ocdj-bottom-player-desktop.png', full_page=True)
    page.get_by_role('link', name='Settings', exact=True).click()
    expect(page).to_have_url(BASE + '/settings')
    expect(player.get_by_text('Fixture artist — SoundCloud segment', exact=True)).to_be_visible()
    page.get_by_role('link', name='Wanted', exact=True).click()
    spotify_row = page.get_by_role('row').filter(has_text='Spotify tone')
    spotify_row.get_by_role('button', name='Play Fixture artist — Spotify tone').click()
    expect(spotify_row.get_by_text('Playing', exact=True)).to_be_visible()
    assert page.evaluate("window.adapterCalls.some(c => c[0] === 'spotify-uri' && c[1] === 'spotify:track:4cOdK2wGLETKBW3PvgPWqT')")
    expect(wanted_row.get_by_text('Playing', exact=True)).to_have_count(0)
    assert not errors, errors
    browser.close()
    print('PASS: native audio, route persistence, queue/previous/next/end, pause/play, error retry, video exclusion, action payloads, source labels, mobile layout, YouTube/SoundCloud adapter fixtures. External provider playback not live-tested.')
