import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { createRequire } from 'node:module';
const require = createRequire(process.env.OCDJ_TEST_JSDOM_PACKAGE || import.meta.url);
const { JSDOM } = require('jsdom');
const roots = ['ocdj-helper', 'OCDJ Helper/OCDJ Helper Extension/Resources'];
const settle = () => new Promise(resolve => setImmediate(resolve));
const source = (id, type = 'track') => `https://www.beatport.com/${type}/synthetic/${id}`;
const embed = (id, type = 'track') => `https://embed.beatport.com/?id=${id}&type=${type}`;
const track = (id = 910001, type = 'track') => ({
  platform: 'beatport', artist: 'Synthetic Artist', title: 'Synthetic (Night) (Extended Mix)',
  source_url: source(id, type), thumb: '', embedUrl: embed(id, type),
});
function worker(root, initial = {}, failWrites = false) {
  let onMessage;
  const stored = structuredClone(initial);
  const listener = { addListener() {} };
  const context = {
    URL, AbortSignal, console, setInterval, clearInterval,
    chrome: {
      runtime: { onStartup: listener, onInstalled: listener, onMessage: { addListener(callback) { onMessage = callback; } }, onConnect: listener },
      storage: { onChanged: listener, local: {
        get: async (keys, callback) => { if (callback) callback(structuredClone(stored)); return structuredClone(stored); },
        set: async data => { if (failWrites) throw new Error('synthetic storage unavailable'); Object.assign(stored, structuredClone(data)); },
      } },
    },
    fetch: async () => { throw new Error('Exact Beatport queue must not call any API or search'); },
  };
  const handlers = runInNewContext(readFileSync(`${root}/background/service-worker.js`, 'utf8') + '\nmessageHandlers;', context);
  return { handlers, stored, dispatch: message => new Promise(resolve => onMessage(message, {}, resolve)) };
}
async function panel(root, playerQueue, service) {
  const dom = new JSDOM(readFileSync(`${root}/side-panel/panel.html`, 'utf8'), {
    url: 'https://extension.invalid/side-panel/panel.html', runScripts: 'outside-only',
  });
  const w = dom.window;
  const stored = service?.stored || { playerQueue: structuredClone(playerQueue), backendUrl: 'https://backend.invalid' };
  const messages = [];
  let storageChanged;
  const listener = { addListener() {} };
  w.chrome = {
    storage: { onChanged: { addListener(callback) { storageChanged = callback; } },
      local: { get: async () => structuredClone(stored), set: async data => Object.assign(stored, structuredClone(data)) } },
    runtime: {
      connect: () => ({ onMessage: listener, onDisconnect: listener }),
      sendMessage: async message => { messages.push(structuredClone(message)); return service ? service.dispatch(structuredClone(message)) : { created: true }; },
    },
  };
  w.eval(readFileSync(`${root}/side-panel/panel.js`, 'utf8'));
  await settle();
  return { w, stored, messages, storageChanged: (...args) => storageChanged(...args), close: () => w.close() };
}
function play(f, index = 0) { f.w.document.querySelectorAll('.queue-item-header .queue-item-actions button')[index * 3].click(); }

for (const root of roots) {
  test(`${root}: exact worker derives official ID, stores mix and source, deduplicates by ID without auth/network`, async () => {
    const { handlers, stored } = worker(root, { digToken: 'synthetic-untouched' });
    const data = track();
    data.embedUrl = 'https://untrusted.invalid/arbitrary';
    data.tracks = [{ title: 'Synthetic untrusted track metadata' }];
    const result = await handlers['dig:queue-embed'](data);
    assert.equal(result.queued, true);
    assert.equal(result.item.embedUrl, embed(910001));
    assert.equal(result.item.title, data.title);
    assert.equal(result.item.source_url, data.source_url);
    assert.equal(result.item.videos.length, 0);
    assert.equal(result.item.tracks.length, 0);
    assert.equal(stored.digToken, 'synthetic-untouched');
    assert.equal((await handlers['dig:queue-embed']({ ...data, source_url: 'https://beatport.com/fr/track/other-slug/910001/' })).duplicate, true);
    await handlers['dig:queue-embed'](track(910002)); // same artist/title, different recording/mix ID
    await handlers['dig:queue-embed'](track(910001, 'release')); // ID namespace must stay distinct
    assert.equal(stored.playerQueue.length, 3);
    assert.equal(stored.playerQueue[2].embedUrl, embed(910001, 'release'));
  });

  test(`${root}: declared content → actual worker message router → persisted queue → panel preserves exact release`, async () => {
    const service = worker(root);
    const dom = new JSDOM('<title>Synthetic Artist - Synthetic Release [Synthetic Label] | Beatport</title><section><h1>Synthetic Release</h1></section>', {
      url: source(910003, 'release'), runScripts: 'outside-only', pretendToBeVisual: true,
    });
    const w = dom.window;
    w.chrome = {
      storage: { local: { get: (keys, done) => done({}) }, onChanged: { addListener() {} } },
      runtime: { sendMessage: (message, done) => service.dispatch(structuredClone(message)).then(done) },
    };
    try {
      const manifest = JSON.parse(readFileSync(`${root}/manifest.json`, 'utf8'));
      const scripts = manifest.content_scripts.find(entry => entry.js?.includes('content/beatport.js')).js;
      w.eval(scripts.map(file => readFileSync(`${root}/${file}`, 'utf8')).join('\n'));
      w.document.querySelector('.ocdj-play-btn').click();
      await settle();
      assert.equal(service.stored.playerQueue.length, 1);
      assert.equal(service.stored.playerQueue[0].embedUrl, embed(910003, 'release'));
      assert.equal(w.document.querySelector('.ocdj-play-btn').title, 'Queued for listening');
      const f = await panel(root, service.stored.playerQueue);
      try {
        play(f);
        assert.equal(f.w.document.querySelector('#embed-player').src, embed(910003, 'release'));
        assert.equal(f.w.document.querySelector('#beatport-preview-source').href, source(910003, 'release'));
      } finally { f.close(); }
    } finally { w.close(); }
  });

  test(`${root}: malformed/foreign/ambiguous sources and legacy search fail without queue mutation`, async () => {
    const { handlers, stored } = worker(root);
    for (const source_url of [undefined, '', 'not a URL', 'http://www.beatport.com/track/synthetic/910001',
      'https://www.beatport.com.evil.invalid/track/synthetic/910001',
      'https://www.beatport.com:8443/track/synthetic/910001',
      'https://fake@www.beatport.com/track/synthetic/910001',
      'https://www.beatport.com/artist/synthetic/910001',
      'https://www.beatport.com/track/synthetic/0', 'https://www.beatport.com/track/synthetic/01',
      'https://www.beatport.com/track/synthetic/not-numeric', 'https://www.beatport.com/release/synthetic/910001/extra']) {
      assert.match((await handlers['dig:queue-embed']({ ...track(), source_url })).error, /Exact Beatport source unavailable/);
    }
    assert.match((await handlers['dig:queue-search'](track())).error, /Exact Beatport source required/);
    assert.equal(stored.playerQueue, undefined);
  });

  test(`${root}: exact requeue is not blocked by an old generic search result`, async () => {
    const old = { ...track(), embedUrl: undefined, videos: [{ videoId: 'synthetic-wrong-recording' }] };
    const { handlers, stored } = worker(root, { playerQueue: [old] });
    assert.equal((await handlers['dig:queue-embed'](track())).queued, true);
    assert.equal(stored.playerQueue.length, 2);
    assert.equal(stored.playerQueue[1].embedUrl, embed(910001));
  });

  test(`${root}: exact queue reports storage failure rather than claiming success`, async () => {
    const { dispatch, stored } = worker(root, {}, true);
    assert.match((await dispatch({ type: 'dig:queue-embed', data: track() })).error, /synthetic storage unavailable/);
    assert.equal(stored.playerQueue, undefined);
  });

  test(`${root}: panel binds exact track and release to direct official frames; Wanted retains release semantics`, async () => {
    const f = await panel(root, [track(), track(910002, 'release')]);
    try {
      play(f);
      await settle();
      assert.equal(f.w.document.querySelector('#embed-player').src, embed(910001));
      assert.equal(f.w.document.querySelector('#player-container').className, 'platform-beatport-track');
      assert.equal(f.w.document.querySelector('#beatport-preview-note').hidden, false);
      assert.equal(f.w.document.querySelector('#beatport-preview-source').href, source(910001));
      play(f, 1);
      await settle();
      assert.equal(f.w.document.querySelector('#embed-player').src, embed(910002, 'release'));
      assert.equal(f.w.document.querySelector('#player-container').className, 'platform-beatport-release');
      f.w.document.querySelectorAll('.dig-btn')[1].click();
      await settle();
      assert.equal(f.messages[0].type, 'dig:add');
      assert.equal(f.messages[0].data.title, '');
      assert.equal(f.messages[0].data.release_name, track().title);
      assert.equal(f.messages[0].data.source_site, 'beatport');
      f.w.document.querySelector('#btn-clear-queue').click();
      await settle();
      assert.equal(f.w.document.querySelector('#beatport-preview-note').hidden, true);
      assert.equal(f.w.document.querySelector('#embed-player').style.display, 'none');
    } finally { f.close(); }
  });

  test(`${root}: panel replaces legacy search identity and rejects unsafe persisted source without playing YouTube`, async () => {
    const old = { ...track(), embedUrl: undefined, videos: [{ videoId: 'synthetic-wrong-recording', title: 'Wrong mix' }] };
    const f = await panel(root, [old, { ...old, source_url: 'https://foreign.invalid/track/synthetic/910001' }]);
    try {
      play(f);
      await settle();
      assert.equal(f.w.document.querySelector('#embed-player').src, embed(910001));
      assert.equal(f.stored.playerQueue[0].videos.length, 0);
      play(f, 1);
      await settle();
      assert.equal(f.w.document.querySelector('#embed-player').style.display, 'none');
      assert.match(f.w.document.querySelector('#player-placeholder').textContent, /Exact Beatport source unavailable/);
      assert.equal(f.w.document.querySelector('#beatport-preview-note').hidden, true);
      assert.equal(f.w.document.querySelector('#now-playing-title').textContent, 'Beatport source unavailable');
      f.w.document.querySelector('#btn-clear-queue').click();
      await settle();
      assert.equal(f.w.document.querySelector('#player-placeholder').textContent, 'Queue a release to start listening');
    } finally { f.close(); }
  });

  test(`${root}: Beatport preview note and classes do not leak to existing Spotify playback`, async () => {
    const f = await panel(root, [track(), { platform: 'spotify', artist: 'Fixture', title: 'Fixture',
      embedUrl: 'https://open.spotify.com/embed/track/synthetic', videos: [] }]);
    try {
      play(f); play(f, 1);
      await settle();
      assert.equal(f.w.document.querySelector('#beatport-preview-note').hidden, true);
      assert.equal(f.w.document.querySelector('#player-container').className, 'platform-spotify-track');
      const frame = f.w.document.querySelector('#embed-player').src;
      if (root === 'ocdj-helper') assert.match(frame, /^https:\/\/backend.invalid\/api\/dig\/embed\//);
      else assert.equal(frame, 'https://open.spotify.com/embed/track/synthetic');
    } finally { f.close(); }
  });

  test(`${root}: removing one queued entry with a real worker does not also remove its neighbor`, async () => {
    const service = worker(root, { playerQueue: [track(910001), track(910002), track(910003)] });
    const f = await panel(root, service.stored.playerQueue, service);
    try {
      f.w.document.querySelectorAll('.remove-btn')[1].click();
      await settle(); await settle();
      assert.deepEqual(f.stored.playerQueue.map(item => item.source_url), [source(910001), source(910003)]);
    } finally { f.close(); }
  });

  test(`${root}: only the official frame host is added; existing API host grants remain unchanged`, () => {
    const manifest = JSON.parse(readFileSync(`${root}/manifest.json`, 'utf8'));
    assert.ok(manifest.content_security_policy.extension_pages.includes('frame-src https://embed.beatport.com '));
    assert.equal(manifest.host_permissions.some(host => /beatport/.test(host)), false);
    assert.equal(manifest.host_permissions.some(host => /bpcdn/.test(host)), false);
  });
}


test('Safari popup offers its existing player resource; storage changes refresh the visible queue without ports', async () => {
  const root = roots[1];
  const popup = new JSDOM(readFileSync(`${root}/popup/popup.html`, 'utf8'), { url: 'https://extension.invalid/popup/popup.html' });
  try {
    const link = popup.window.document.querySelector('#queue-link');
    assert.equal(link.href, 'https://extension.invalid/side-panel/panel.html');
    assert.equal(link.target, '_blank');
    assert.equal(link.rel, 'noopener');
  } finally { popup.window.close(); }
  const f = await panel(root, []);
  try {
    assert.equal(f.w.document.querySelectorAll('.queue-item').length, 0);
    f.stored.playerQueue = [track(910004, 'release')];
    f.storageChanged({ playerQueue: { newValue: f.stored.playerQueue } }, 'local');
    await settle();
    assert.equal(f.w.document.querySelectorAll('.queue-item').length, 1);
    play(f);
    assert.equal(f.w.document.querySelector('#embed-player').src, embed(910004, 'release'));
    assert.doesNotMatch(readFileSync(`${root}/side-panel/panel.js`, 'utf8'), /runtime\.connect|setTimeout\(connectPort/);
  } finally { f.close(); }
});
