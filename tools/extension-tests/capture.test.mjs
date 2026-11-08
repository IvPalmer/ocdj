import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

// Reuse a locally installed DOM test tool; no extension/runtime dependency.
const require = createRequire(process.env.OCDJ_TEST_JSDOM_PACKAGE || import.meta.url);
const { JSDOM } = require('jsdom');
const roots = ['ocdj-helper', 'OCDJ Helper/OCDJ Helper Extension/Resources'];
const pause = () => new Promise(resolve => setTimeout(resolve, 350));
function fixture(root, site, url, body, head = '') {
  const dom = new JSDOM(`<html><head>${head}</head><body>${body}</body></html>`, {
    url, runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const w = dom.window;
  const messages = [];
  w.chrome = { runtime: { sendMessage(message, done) {
    messages.push(message);
    done({ created: true, queued: true, item: message.data });
  } } };
  w.eval(readFileSync(`${root}/content/shared.js`, 'utf8') + '\nwindow.OCDJ = OCDJ;');
  w.eval(readFileSync(`${root}/content/${site}.js`, 'utf8'));
  return { dom, w, messages, close: async () => { await new Promise(resolve => setImmediate(resolve)); w.close(); } };
}
function click(w, selector) {
  const button = w.document.querySelector(selector);
  assert.ok(button, `missing ${selector}`);
  button.click();
}
for (const root of roots) {
  test(`${root}: YouTube delayed controls, SPA reuse, remount and source`, async () => {
    const f = fixture(root, 'youtube', 'https://www.youtube.com/watch?v=first', '<h1 class="title">Artist - First</h1>');
    try {
      f.w.document.body.insertAdjacentHTML('beforeend', '<div id="actions"><div id="actions-inner"></div></div>');
      await pause();
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 1);
      click(f.w, '.ocdj-play-btn');
      assert.equal(f.messages.at(-1).data.videoId, 'first');
      f.w.history.pushState({}, '', '/watch?v=second');
      f.w.document.querySelector('h1').textContent = 'Artist - Second';
      await pause();
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 1);
      click(f.w, '.ocdj-dig-btn');
      assert.equal(f.messages.at(-1).data.title, 'Second');
      assert.equal(f.messages.at(-1).data.source_url, 'https://www.youtube.com/watch?v=second');
      f.w.document.querySelector('#actions-inner').replaceChildren();
      await pause();
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 1);
    } finally { await f.close(); }
  });
  test(`${root}: Spotify heading fallback and native embed`, async () => {
    const f = fixture(root, 'spotify', 'https://open.spotify.com/track/abc', '<main><header><h1>New song</h1><a href="/artist/a">Artist</a></header></main>');
    try {
      click(f.w, '.ocdj-play-btn'); click(f.w, '.ocdj-dig-btn');
      assert.equal(f.messages[0].type, 'dig:queue-embed');
      assert.equal(f.messages[0].data.embedUrl, 'https://open.spotify.com/embed/track/abc?theme=0');
      assert.equal(f.messages[1].data.source_site, 'spotify');
      assert.equal(f.messages[1].data.title, 'New song');
      await pause();
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 1);
    } finally { await f.close(); }
  });
  test(`${root}: Spotify row source and recycled row closures`, async () => {
    const f = fixture(root, 'spotify', 'https://open.spotify.com/album/album1', '<header><h1>Album</h1></header><div data-testid="tracklist-row"><a data-testid="internal-track-link" href="/track/first"><div>First</div></a></div>');
    try {
      click(f.w, '[data-testid="tracklist-row"] .ocdj-dig-btn');
      assert.equal(f.messages.at(-1).data.source_url, 'https://open.spotify.com/track/first');
      const link = f.w.document.querySelector('[data-testid="internal-track-link"]');
      link.href = '/track/second'; link.querySelector('div').textContent = 'Second';
      await pause();
      click(f.w, '[data-testid="tracklist-row"] .ocdj-play-btn');
      assert.equal(f.messages.at(-1).data.embedUrl, 'https://open.spotify.com/embed/track/second?theme=0');
      assert.equal(f.w.document.querySelectorAll('[data-testid="tracklist-row"] .ocdj-btn-group').length, 1);
    } finally { await f.close(); }
  });
  test(`${root}: SoundCloud late mount and native embed`, async () => {
    const f = fixture(root, 'soundcloud', 'https://soundcloud.com/artist/song', '');
    try {
      f.w.document.body.innerHTML = '<div class="soundTitle"><span class="soundTitle__usernameText">Artist</span><div class="soundTitle__title"><span>Artist - Song</span></div></div>';
      await pause();
      click(f.w, '.ocdj-play-btn'); click(f.w, '.ocdj-dig-btn');
      assert.equal(f.messages[0].data.platform, 'soundcloud');
      assert.match(f.messages[0].data.embedUrl, /artist%2Fsong/);
      assert.equal(f.messages[1].data.title, 'Song');
      assert.equal(f.messages[1].data.source_site, 'soundcloud');
    } finally { await f.close(); }
  });
  test(`${root}: Bandcamp sibling guard survives repeated host updates`, async () => {
    const f = fixture(root, 'bandcamp', 'https://artist.bandcamp.com/album/release', '<section><div class="buyButtons"></div></section><div data-tralbum=\'{"artist":"Artist","id":123,"current":{"title":"Release","type":"album"}}\'></div>');
    try {
      for (let i = 0; i < 2; i++) {
        f.w.document.body.appendChild(f.w.document.createElement('p'));
        await pause();
      }
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 1);
      click(f.w, '.ocdj-play-btn'); click(f.w, '.ocdj-dig-btn');
      assert.match(f.messages[0].data.embedUrl, /album=123/);
      assert.equal(f.messages[1].data.release_name, 'Release');
      assert.equal(f.messages[1].data.source_site, 'bandcamp');
    } finally { await f.close(); }
  });
  test(`${root}: Beatport waits for matching metadata, preserves mix and source`, async () => {
    const f = fixture(root, 'beatport', 'https://www.beatport.com/track/so-fly/123', '<main><section><h1>So Fly Extended</h1></section></main>', '<title>Old - Track | Beatport</title>');
    try {
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 0);
      f.w.document.title = 'Riton, Kid Enigma, RUZE - So Fly (Extended) [Label] | Music & Downloads on Beatport';
      await pause();
      click(f.w, '.ocdj-play-btn'); click(f.w, '.ocdj-dig-btn');
      assert.equal(f.messages[0].type, 'dig:queue-search');
      assert.equal(f.messages[0].data.platform, 'beatport');
      assert.equal(f.messages[0].data.title, 'So Fly (Extended)');
      assert.equal(f.messages[1].data.source_site, 'beatport');
      assert.equal(f.messages[1].data.source_url, 'https://www.beatport.com/track/so-fly/123');
      f.w.history.pushState({}, '', '/genre/house/5');
      f.w.document.querySelector('h1').textContent = 'House';
      await pause();
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 0);
    } finally { await f.close(); }
  });
}
for (const root of roots) {
  test(`${root}: Discogs existing release actions survive shared utility changes`, async () => {
    const f = fixture(root, 'discogs', 'https://www.discogs.com/release/123-Artist-Release', '<h1>Artist – Release</h1>', '<title>Artist – Release | Discogs</title><script type="application/ld+json">{"@type":"MusicAlbum","name":"Release","byArtist":{"name":"Artist"}}</script>');
    try {
      click(f.w, '.ocdj-play-btn'); click(f.w, '.ocdj-dig-btn');
      await pause();
      assert.equal(f.messages[0].type, 'dig:queue');
      assert.equal(f.messages[0].data.releaseId, 123);
      assert.equal(f.messages[1].data.release_name, 'Release');
      assert.equal(f.messages[1].data.source_site, 'discogs');
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 1);
    } finally { await f.close(); }
  });
  test(`${root}: missing worker replies restore Wantlist and Queue for retry`, async () => {
    const f = fixture(root, 'spotify', 'https://open.spotify.com/track/abc', '<header><h1>Song</h1><a href="/artist/a">Artist</a></header>');
    try {
      f.w.chrome.runtime.sendMessage = (message, done) => done(undefined);
      for (const selector of ['.ocdj-dig-btn', '.ocdj-play-btn']) {
        click(f.w, selector);
        await new Promise(resolve => setImmediate(resolve));
        const button = f.w.document.querySelector(selector);
        assert.equal(button.textContent, selector.includes('dig') ? 'Wantlist' : 'Queue');
        assert.doesNotMatch(button.className, /--loading|--added|--queued/);
      }
      assert.match(f.w.document.querySelector('.ocdj-toast').textContent, /No answer from the extension/);
      f.w.chrome.runtime.sendMessage = (message, done) => done({ created: true, queued: true, item: message.data });
      click(f.w, '.ocdj-dig-btn'); click(f.w, '.ocdj-play-btn');
      await new Promise(resolve => setImmediate(resolve));
      assert.match(f.w.document.querySelector('.ocdj-dig-btn').className, /--added/);
      assert.match(f.w.document.querySelector('.ocdj-play-btn').className, /--queued/);
    } finally { await f.close(); }
  });
  test(`${root}: Beatport release payload and offline acknowledgment`, async () => {
    const f = fixture(root, 'beatport', 'https://www.beatport.com/release/release/123', '<section><h1>Release</h1></section>', '<title>Artist - Release [Label] | Beatport</title>');
    try {
      f.w.chrome.runtime.sendMessage = (message, done) => {
        f.messages.push(message);
        done({ created: true, offline: true, item: message.data });
      };
      click(f.w, '.ocdj-dig-btn');
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(f.messages[0].data.title, '');
      assert.equal(f.messages[0].data.release_name, 'Release');
      assert.match(f.w.document.querySelector('.ocdj-dig-btn').title, /Saved locally/);
      assert.match(f.w.document.querySelector('.ocdj-toast').textContent, /not in Wanted List/);
    } finally { await f.close(); }
  });
}

const { runInNewContext } = await import('node:vm');
for (const root of roots) {
  test(`${root}: queue search calls API, stores source, deduplicates and reports empty search`, async () => {
    const stored = {};
    const calls = [];
    const listener = { addListener() {} };
    let response = { videoId: 'test-video', title: 'Search match' };
    const context = {
      AbortSignal, console, setInterval, clearInterval,
      chrome: {
        runtime: { onStartup: listener, onInstalled: listener, onMessage: listener, onConnect: listener },
        storage: { onChanged: listener, local: {
          get: async (keys, callback) => { if (callback) callback(stored); return stored; },
          set: async data => Object.assign(stored, data),
        } },
      },
      fetch: async (url, options) => {
        calls.push({ url, options });
        return { ok: true, json: async () => response };
      },
    };
    const handlers = runInNewContext(readFileSync(`${root}/background/service-worker.js`, 'utf8') + '\nmessageHandlers;', context);
    const data = { artist: 'Artist', title: 'Track (Extended)', platform: 'beatport', source_url: 'https://www.beatport.com/track/track/123' };
    const result = await handlers['dig:queue-search'](data);
    assert.equal(result.queued, true);
    assert.match(calls[0].url, /\/api\/dig\/yt-search\/\?q=Artist%20Track%20/);
    assert.equal(stored.playerQueue[0].source_url, data.source_url);
    assert.equal(stored.playerQueue[0].platform, 'beatport');
    assert.equal(stored.playerQueue[0].videos[0].videoId, 'test-video');
    await handlers['dig:queue-search'](data);
    assert.equal(stored.playerQueue.length, 1);
    response = {};
    assert.equal((await handlers['dig:queue-search'](data)).error, 'No results found');
    assert.equal(stored.playerQueue.length, 1);
  });
}
