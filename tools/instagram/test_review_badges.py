"""Instagram review badges against synthetic responses only; no DB or API server.

Run with Vite serving the frontend:
    python3 tools/instagram/test_review_badges.py http://127.0.0.1:5188
Uses installed Chrome locally, Playwright Chromium in CI. Optional screenshots:
    INSTAGRAM_BADGE_STILLS=/tmp/instagram-badges python3 ...
"""
import io
import json
import os
from pathlib import Path
import sys
import wave

from playwright.sync_api import sync_playwright, expect

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:5188'


def fixture_post(post_id, author, flags, images=0, **extra):
    media = [dict(id=post_id * 10 + i, kind='video', position=i,
                  status='pending', reviewed=flag, artist='', title='', candidates=[])
             for i, flag in enumerate(flags)]
    media += [dict(id=post_id * 10 + len(flags) + i, kind='image',
                   position=len(flags) + i, status='pending', reviewed=True,
                   artist='', title='', candidates=[]) for i in range(images)]
    return dict(id=post_id, author=author, caption='Synthetic saved carousel',
                permalink='https://example.invalid/post/', media=media, **extra)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel=None if os.getenv('CI') else 'chrome')
    for width in (1280, 390):
        for layout in ('Grid', 'List'):
            page = browser.new_page(viewport=dict(width=width, height=900))
            page.set_default_timeout(3000)
            errors, writes = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            # No review aggregate fields: these are also valid legacy API responses.
            posts = [fixture_post(1, 'untouched', [False] * 4),
                     fixture_post(2, 'partial', [True, False, False, False], images=1,
                                  thumbnail_url='/api/fixture-bright.svg'),
                     fixture_post(3, 'complete', [True] * 4, images=1,
                                  thumbnail_url='/api/fixture-dark.svg'),
                     fixture_post(4, 'images_only', [], images=1),
                     fixture_post(5, 'stale_aggregate', [False], reviewed=True,
                                  reviewed_count=99, video_count=99)]

            def route(r):
                req = r.request
                path = req.url.split('/api/', 1)[1].split('?', 1)[0]
                if path in ('fixture-bright.svg', 'fixture-dark.svg'):
                    color = '#fff' if 'bright' in path else '#000'
                    r.fulfill(content_type='image/svg+xml', body=
                              f'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="180"><rect width="240" height="180" fill="{color}"/></svg>')
                    return
                data = dict(results=[], count=0)
                if path == 'instagram/' and req.method == 'GET':
                    data = dict(fixture=True, connector_status='offline', posts=posts)
                elif req.method == 'PATCH' and path.startswith('instagram/media/'):
                    media_id = int(path.split('/')[2])
                    payload = req.post_data_json
                    assert set(payload) == {'reviewed'}
                    media = next(m for post in posts for m in post['media'] if m['id'] == media_id)
                    media.update(payload)
                    writes.append((media_id, payload['reviewed']))
                    data = media
                else:
                    assert req.method == 'GET', (path, req.method)
                r.fulfill(status=200, content_type='application/json', body=json.dumps(data))

            # Every API response is synthetic, and all external requests are blocked.
            page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(BASE + '/') else r.abort())
            page.route(BASE + '/api/**', route)
            page.goto(BASE + '/instagram')
            page.get_by_role('group', name='Post layout').get_by_role('button', name=layout, exact=True).click()
            queue = page.get_by_role('navigation', name='Saved posts')

            def card(author):
                return queue.get_by_role('button').filter(has=page.get_by_text('@' + author, exact=True))

            def badge(author, text=None):
                status = card(author).locator('.ig-review-badge')
                if text is None:
                    expect(status).to_have_count(0)
                else:
                    expect(status).to_have_text(text, timeout=2000)
                    expect(status).to_be_visible()
                    # Review text appears once on each card.
                    assert card(author).inner_text().lower().count('reviewed') == 1

            badge('untouched')
            badge('partial', '1/4 reviewed')
            badge('complete', '✓Reviewed')
            badge('images_only')
            badge('stale_aggregate')
            if layout == 'Grid':
                for author in ('partial', 'complete'):
                    thumbnail = card(author).locator('.ig-cover img')
                    expect(thumbnail).to_be_visible()
                    expect(thumbnail).to_have_js_property('naturalWidth', 240)
            # Each badge stays inside the card, independent of thumbnail colors.
            for author in ('partial', 'complete'):
                assert card(author).locator('.ig-review-badge').evaluate('''badge => {
                    const rect = badge.getBoundingClientRect();
                    const card = badge.closest('button').getBoundingClientRect();
                    return rect.left >= card.left && rect.right <= card.right;
                }''')
            expect(card('untouched').get_by_text('4 videos', exact=True)).to_be_visible()
            # Zero -> partial -> complete -> partial -> zero through real UI mutations.
            card('untouched').click()
            for i in range(4):
                page.get_by_role('button', name=f'video {i + 1}, pending', exact=True).click()
                page.get_by_role('button', name='Mark reviewed', exact=True).click()
                badge('untouched', '✓Reviewed' if i == 3 else f'{i + 1}/4 reviewed')
            for i in range(3, -1, -1):
                page.get_by_role('button', name=f'video {i + 1}, pending, reviewed', exact=True).click()
                page.get_by_role('button', name='Mark unreviewed', exact=True).click()
                badge('untouched', f'{i}/4 reviewed' if i else None)
            assert writes == [(10 + i, True) for i in range(4)] + [(10 + i, False) for i in range(3, -1, -1)]
            # Reviewed retains its per-media semantics, including partial carousels.
            page.get_by_role('group', name='Filter media').get_by_role('button', name='Reviewed', exact=True).click()
            expect(card('untouched')).to_have_count(0)
            badge('partial', '1/4 reviewed')
            badge('complete', '✓Reviewed')
            page.get_by_role('group', name='Filter media').get_by_role('button', name='All', exact=True).click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            if os.getenv('INSTAGRAM_BADGE_STILLS'):
                folder = Path(os.environ['INSTAGRAM_BADGE_STILLS'])
                folder.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(folder / f'{layout.lower()}-{width}.png'), full_page=True)
            assert not errors, errors
            page.close()
            print(f'PASS: {layout} at {width}px: legacy flags, zero/partial/full, mark/unmark, filters, no overflow')
    browser.close()

# A separate ready connector fixture lets the browser drive requests, with
# connector completion controlled independently of selection.
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel=None if os.getenv('CI') else 'chrome')
    page = browser.new_page()
    page.clock.install()
    posts = [fixture_post(1, 'first', [False] * 3), fixture_post(2, 'second', [False])]
    requests = []
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(bytes(16000))

    def playback_route(r):
        path = r.request.url.split('/api/', 1)[1]
        data = dict(results=[], count=0)
        if path == 'instagram/':
            data = dict(fixture=True, connector_status='ready', posts=posts)
        elif path.endswith('/playback/'):
            media_id = int(path.split('/')[2])
            requests.append(media_id)
            data = next(m for post in posts for m in post['media'] if m['id'] == media_id)
            data['playback_requested'] = True
        elif path.startswith('fixture-video/'):
            r.fulfill(content_type='audio/wav', body=audio.getvalue())
            return
        r.fulfill(json=data)

    page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(BASE + '/') else r.abort())
    page.route(BASE + '/api/**', playback_route)
    page.goto(BASE + '/instagram')
    cards = page.get_by_role('navigation', name='Saved posts').get_by_role('button')
    expect(cards).to_have_count(2)
    page.clock.run_for(1000)
    assert requests == [], requests

    cards.first.click()
    page.clock.run_for(1000)
    expect(page.get_by_text('Getting video ready…')).to_be_visible()
    assert requests == [10], requests

    def finish(clip):
        clip.update(playback_requested=False, playback_url=f"/api/fixture-video/{clip['id']}",
                    playback_version=str(clip['id']))
        page.get_by_role('button', name='Refresh status').click()

    finish(posts[0]['media'][0])
    video = page.locator('.ig-player video')
    expect(video).to_have_attribute('src', '/api/fixture-video/10')
    page.wait_for_function('document.querySelector(".ig-player video").readyState >= 1')
    expect(video).to_have_js_property('paused', True)
    page.clock.run_for(1000)
    assert requests == [10, 11], requests

    finish(posts[0]['media'][1])
    page.clock.run_for(1000)
    assert requests == [10, 11], requests
    page.get_by_role('button', name='video 2, pending', exact=True).click()
    expect(video).to_have_attribute('src', '/api/fixture-video/11')
    expect(video).to_have_js_property('paused', True)
    page.clock.run_for(1000)
    assert requests == [10, 11, 12], requests
    browser.close()
    print('PASS: selection loads one video, prepares one next clip, and leaves playback paused')
