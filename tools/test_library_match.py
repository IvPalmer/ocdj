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
    errors, lookups, writes = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    titles = ['Measured tone', 'Unknown quality', 'Multiple tone', 'Missing tone', 'Absent tone', 'Failed lookup', '']
    items = [dict(id=i + 1, artist='Fixture artist', title=title, status='pending',
                  added='2026-01-01T00:00:00Z', preview_checked='2026-01-01T00:00:00Z') for i, title in enumerate(titles)]
    job = dict(id=1, status='completed', title='Synthetic Shazam job', url='https://example.invalid/mix',
               engine='shazam', tracklist=[dict(artist='Fixture artist', title='Measured tone',
               timestamp_start=12, confidence='high')], description_tracks=[])

    def route(r):
        req = r.request
        parsed = urlparse(req.url)
        path = parsed.path.removeprefix('/api/')
        if req.method != 'GET':
            writes.append(path)
        data = dict(results=[], count=0)
        if path == 'wanted/items/':
            data = dict(count=len(items), results=items)
        elif path == 'wanted/shazam/status/':
            data = dict(total=len(items), spotify_connected=True, overdue=False)
        elif path == 'recognize/jobs/':
            data = dict(results=[job])
        elif path == 'recognize/jobs/1/':
            data = job
        elif path == 'library/match/':
            params = parse_qs(parsed.query)
            assert params['artist'] == ['Fixture artist'], params
            title = params['title'][0]
            lookups.append(title)
            if title == 'Failed lookup':
                r.fulfill(status=503, content_type='application/json', body='{}')
                return
            record = dict(id=1, format='flac', bitrate=None, sample_rate=44100)
            states = {
                'Measured tone': ('matched', [record]),
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
    expect(row('Measured tone').get_by_text('Indexed match · FLAC · 44.1 kHz', exact=True)).to_be_visible()
    expect(row('Unknown quality').get_by_text('Indexed match · MP3 · Quality unknown', exact=True)).to_be_visible()
    expect(row('Multiple tone').get_by_text('Multiple indexed matches · version unverified', exact=True)).to_be_visible()
    expect(row('Missing tone').get_by_text('Indexed file marked missing', exact=True)).to_be_visible()
    expect(row('Absent tone').get_by_text('No indexed match', exact=True)).to_be_visible()
    expect(row('Failed lookup').get_by_text('Library check unavailable', exact=True)).to_be_visible()
    expect(page.get_by_text('Artist + title needed for library check', exact=True)).to_be_visible()
    assert '' not in lookups
    expect(row('Measured tone').get_by_text('Unindexed collections unknown', exact=True)).to_be_visible()
    assert row('Measured tone').get_by_role('button', name='Play Fixture artist — Measured tone', exact=True).count() == 0
    page.get_by_role('link', name='Recognize', exact=True).click()
    expect(page.get_by_text('Indexed match · FLAC · 44.1 kHz', exact=True)).to_be_visible()
    assert page.get_by_role('button', name='Play Fixture artist — Measured tone', exact=True).count() == 0
    assert lookups.count('Measured tone') == 1, lookups  # shared cache across surfaces
    page.get_by_role('link', name='Shazam', exact=True).click()
    expect(page.get_by_text('Indexed match · FLAC · 44.1 kHz', exact=True)).to_be_visible()
    assert lookups.count('Measured tone') == 1, lookups
    page.get_by_role('link', name='Recognize', exact=True).click()
    expect(page.get_by_text('Indexed match · FLAC · 44.1 kHz', exact=True)).to_be_visible()
    page.set_viewport_size(dict(width=390, height=844))
    expect(page.get_by_role('button', name='Open navigation', exact=True)).to_have_attribute('aria-expanded', 'false')
    page.wait_for_function("document.querySelector('.sidebar').getBoundingClientRect().right <= 0")
    expect(page.get_by_text('Indexed match · FLAC · 44.1 kHz', exact=True)).to_be_visible()
    page.screenshot(path='/tmp/ocdj-library-match-mobile.png', full_page=True)
    page.set_viewport_size(dict(width=1280, height=900))
    page.screenshot(path='/tmp/ocdj-library-match-desktop.png', full_page=True)
    assert not errors, errors
    assert not writes, writes
    browser.close()
    print('PASS: Wanted + Shazam availability, measured/unknown quality, duplicates, missing/no match, failure, incomplete metadata, shared cache, no invented playback or writes.')
