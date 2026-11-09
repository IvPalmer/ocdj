"""Synthetic UI fixture only. No backend, DB, media or external network access.
Run against Vite: python3 tools/test_library_match.py http://127.0.0.1:5187
"""
import json
import os
import sys
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:5187'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel=None if os.getenv('CI') else 'chrome')
    page = browser.new_page()
    page.set_default_timeout(10000)
    errors, lookups, writes, pending = [], [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    titles = ['Measured tone', 'Unknown quality', 'Multiple tone', 'Missing tone', 'Absent tone', 'Failed lookup', 'Bitrate tone', 'Loading tone', '']
    items = [dict(id=i + 1, artist='Fixture artist', title=title, status='pending',
                  added='2026-01-01T00:00:00Z', preview_checked='2026-01-01T00:00:00Z',
                  preview_url='/api/fixture.wav' if title in ('Measured tone', 'Unknown quality', 'Absent tone') else '') for i, title in enumerate(titles)]
    job = dict(id=1, status='completed', title='Synthetic Shazam job', url='https://example.invalid/mix',
               engine='shazam', tracklist=[dict(artist='Fixture artist', title='Measured tone',
               timestamp_start=12, confidence='high')], description_tracks=[])

    def route(r):
        req = r.request
        parsed = urlparse(req.url)
        path = parsed.path.removeprefix('/api/')
        if req.method != 'GET':
            writes.append((path, req.post_data_json))
        data = dict(results=[], count=0)
        if path == 'wanted/items/':
            data = dict(count=len(items), results=items)
        elif path == 'wanted/shazam/status/':
            data = dict(total=len(items), spotify_connected=True, overdue=False)
        elif path == 'recognize/jobs/':
            data = dict(results=[job])
        elif path == 'recognize/jobs/1/':
            data = job
        elif path == 'soulseek/queue/add/':
            data = []
        elif path == 'library/match/':
            params = parse_qs(parsed.query)
            assert params['artist'] == ['Fixture artist'], params
            title = params['title'][0]
            lookups.append(title)
            if title == 'Loading tone':
                pending.append(r)
                return
            if title == 'Failed lookup':
                r.fulfill(status=503, content_type='application/json', body='{}')
                return
            record = dict(id=1, format='flac', bitrate=None, sample_rate=44100)
            states = {
                'Measured tone': ('matched', [record]),
                'Bitrate tone': ('matched', [dict(id=4, format='mp3', bitrate=320, sample_rate=48000)]),
                'Unknown quality': ('matched', [dict(id=2, format='mp3', bitrate=None, sample_rate=None)]),
                'Multiple tone': ('multiple_matches', [record, dict(id=3, format='mp3', bitrate=320, sample_rate=48000)]),
                'Missing tone': ('indexed_missing', []),
                'Absent tone': ('no_indexed_match', []),
            }
            status, matches = states[title]
            data = dict(status=status, coverage='indexed_only', matches=matches)
        r.fulfill(status=200, content_type='application/json', body=json.dumps(data))

    page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(BASE + '/') else r.abort())
    page.route(BASE + '/api/**', route)
    page.goto(BASE + '/wanted')
    row = lambda title: page.locator('tbody tr').filter(has_text=title)
    def indicator(title, label):
        return row(title).get_by_role('img', name=label)

    checks = [
        ('Measured tone', 'Indexed match · FLAC · 44.1 kHz', 'FLAC · 44.1 kHz'),
        ('Unknown quality', 'Indexed match · MP3 · Quality unknown', ''),
        ('Multiple tone', 'Multiple indexed matches · version unverified', ''),
        ('Missing tone', 'Indexed file marked missing', ''),
        ('Absent tone', 'No indexed match', ''),
        ('Failed lookup', 'Library check unavailable', ''),
        ('Bitrate tone', 'Indexed match · MP3 · 320 kbps · 48 kHz', 'MP3 · 320 kbps'),
        ('Loading tone', 'Checking library index…', ''),
    ]
    for title, label, visible in checks:
        badge = indicator(title, label)
        expect(badge).to_be_visible()
        expect(badge).to_have_text(visible)
        expect(badge).to_have_attribute('title', badge.get_attribute('aria-label'))
        assert 'Unindexed collections unknown' in badge.get_attribute('aria-label')
        assert row(title).get_by_role('img').count() == 1
    expect(page.get_by_role('img', name='Artist + title needed for library check')).to_be_visible()
    assert '' not in lookups
    assert not writes, writes

    # Native keyboard tab stop exposes the full name and a visible focus ring.
    badge = indicator('Measured tone', 'Indexed match')
    row('Measured tone').locator('input').focus()
    page.keyboard.press('Tab')
    expect(row('Measured tone').get_by_role('button', name='Play Fixture artist — Measured tone', exact=True)).to_be_focused()
    for _ in range(5):
        page.keyboard.press('Tab')
    expect(badge).to_be_focused()
    assert badge.evaluate("el => getComputedStyle(el).outlineStyle") == 'solid'

    metrics = {}
    for width, height, name in [(1280, 900, 'desktop'), (390, 844, 'mobile')]:
        page.set_viewport_size(dict(width=width, height=height))
        page.wait_for_function("document.querySelector('.sidebar').getBoundingClientRect().right <= 0" if width < 768 else "document.querySelector('.sidebar').getBoundingClientRect().right > 0")
        cells = page.locator('.wanted-table tbody tr').first
        buttons = cells.get_by_role('button')
        assert buttons.count() == 5
        boxes = [b.bounding_box() for b in buttons.all()]
        assert max(b['height'] for b in boxes) <= (44 if name == 'mobile' else 28), boxes
        if name == 'mobile':
            assert min(b['height'] for b in boxes) >= 44, boxes
        centers = [b['y'] + b['height'] / 2 for b in boxes]
        assert max(centers) - min(centers) < 1, boxes
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if name == 'desktop':
            assert page.locator('.wanted-table').bounding_box()['x'] + page.locator('.wanted-table').bounding_box()['width'] <= width
        expect(page.get_by_role('columnheader').last).to_have_text('Library')
        assert cells.locator('td').count() == 9
        assert cells.locator('td').last.locator('.track-library-match').count() == 1
        # Quality and absent states share identical action positions and row centers.
        for title in ('Unknown quality', 'Absent tone'):
            other = row(title).get_by_role('button')
            other_boxes = [b.bounding_box() for b in other.all()]
            assert [round(b['x'], 1) for b in boxes] == [round(b['x'], 1) for b in other_boxes]
        badge_box = badge.bounding_box()
        assert badge_box['x'] > boxes[-1]['x'] + boxes[-1]['width']
        assert abs(badge_box['y'] + badge_box['height'] / 2 - centers[0]) < 1
        metrics[name] = dict(button_heights=[b['height'] for b in boxes], row_height=cells.bounding_box()['height'])
        page.screenshot(path=f'/tmp/ocdj-library-match-{name}.png', full_page=True)
        # Scroll just the table to reach actions on mobile; keyboard focus follows.
        cells.get_by_role('button', name='Edit', exact=True).focus()
        page.keyboard.press('Enter')
        expect(page.get_by_role('heading', name='Edit Item', exact=True)).to_be_visible()
        page.get_by_role('button', name='Cancel', exact=True).click()
        badge.focus()
        expect(badge).to_be_focused()
        badge.scroll_into_view_if_needed()
        page.screenshot(path=f'/tmp/ocdj-library-match-{name}-actions.png', full_page=True)

    for request in pending:
        request.fulfill(status=200, content_type='application/json', body=json.dumps(dict(
            status='no_indexed_match', coverage='indexed_only', matches=[])))

    # Ordinary Wanted actions, with all traffic intercepted. No real API writes.
    page.set_viewport_size(dict(width=1280, height=900))
    row('Measured tone').get_by_role('button', name='Queue Fixture artist — Measured tone', exact=True).click()
    expect(page.get_by_role('region', name='Persistent player').get_by_text('Fixture artist — Measured tone', exact=True)).to_be_visible()
    # Intercept generated media too; playback is covered by the existing player fixture.
    page.route(BASE + '/api/fixture.wav', lambda r: r.fulfill(status=404, body=''))
    row('Measured tone').get_by_role('button', name='Play Fixture artist — Measured tone', exact=True).click()
    row('Measured tone').get_by_role('button', name='Find', exact=True).click()
    expect(row('Measured tone').get_by_role('button', name='Find', exact=True)).to_be_enabled()
    row('Absent tone').get_by_role('button', name='Del', exact=True).click()
    expect(row('Absent tone').get_by_role('button', name='Del', exact=True)).to_be_enabled()
    assert writes == [('soulseek/queue/add/', {'wanted_item_ids': [1]}),
                      ('wanted/items/5/', None)], writes

    for link in ['Recognize', 'Shazam', 'Recognize']:
        page.get_by_role('link', name=link, exact=True).click()
        expect(page.get_by_role('img', name='Indexed match · FLAC · 44.1 kHz')).to_be_visible()
        assert lookups.count('Measured tone') == 1, lookups
    page.set_viewport_size(dict(width=390, height=844))
    expect(page.get_by_role('button', name='Open navigation', exact=True)).to_have_attribute('aria-expanded', 'false')
    expect(page.get_by_role('img', name='Indexed match · FLAC · 44.1 kHz')).to_be_visible()
    page.screenshot(path='/tmp/ocdj-library-match-recognize-mobile.png', full_page=True)
    assert not errors, errors
    browser.close()
    print('PASS: normal Wanted/Recognize/Shazam routes; one accessible icon per state; measured-only quality; loading/error/missing/ambiguity/coverage details; desktop/mobile alignment, keyboard, edit/play/queue/find/delete; shared cache. Synthetic API only.')
    print(json.dumps(metrics, sort_keys=True))
