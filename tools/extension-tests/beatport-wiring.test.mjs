import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(process.env.OCDJ_TEST_JSDOM_PACKAGE || import.meta.url);
const { JSDOM } = require('jsdom');
const roots = ['ocdj-helper', 'OCDJ Helper/OCDJ Helper Extension/Resources'];
const pause = () => new Promise(resolve => setTimeout(resolve, 350));
for (const root of roots) {
  const manifest = JSON.parse(readFileSync(`${root}/manifest.json`));
  test(`${root}: Beatport declaration loads complete ordered resources on exact HTTPS hosts`, () => {
    const declarations = manifest.content_scripts.filter(entry => entry.js?.includes('content/beatport.js'));
    assert.equal(declarations.length, 1);
    assert.deepEqual(declarations[0], {
      matches: ['https://www.beatport.com/*', 'https://beatport.com/*'],
      js: ['content/shared.js', 'content/beatport.js'],
      css: ['styles/injected.css'], run_at: 'document_idle',
    });
    for (const entry of manifest.content_scripts) {
      for (const file of [...(entry.js || []), ...(entry.css || [])]) assert.ok(existsSync(`${root}/${file}`), file);
    }
  });

  test(`${root}: options round-trip Beatport and existing disabled sites without changing auth`, async () => {
    const dom = new JSDOM(readFileSync(`${root}/options/options.html`, 'utf8'), { runScripts: 'outside-only' });
    const w = dom.window;
    const stored = { siteSettings: { beatport: false, spotify: false }, digToken: 'synthetic-token', discogsToken: 'synthetic-discogs' };
    let saved;
    w.chrome = { storage: { local: { get: async () => stored, set: async data => { saved = data; } } } };
    try {
      w.eval(readFileSync(`${root}/options/options.js`, 'utf8'));
      await new Promise(resolve => setImmediate(resolve));
      const toggle = w.document.querySelector('#site-beatport');
      assert.ok(toggle);
      assert.equal(toggle.checked, false);
      toggle.checked = true;
      w.document.querySelector('#save-btn').click();
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(saved.siteSettings.beatport, true);
      assert.equal(saved.siteSettings.spotify, false);
      if (root !== 'ocdj-helper') {
        assert.equal(saved.digToken, stored.digToken);
        assert.equal(saved.discogsToken, stored.discogsToken);
      }
    } finally { w.close(); }
  });

  // Execute scripts from the manifest, rather than loading an undeclared adapter.
  function page(path, title, heading, canonical = '', enabled = true) {
    const dom = new JSDOM(`<title>${title}</title><main><section><h1>${heading}</h1></section></main>`, {
      url: `https://www.beatport.com${path}`, runScripts: 'outside-only', pretendToBeVisual: true,
    });
    const w = dom.window;
    if (canonical) {
      const link = w.document.createElement('link'); link.rel = 'canonical'; link.href = canonical;
      w.document.head.append(link);
    }
    const messages = [];
    let changeSettings;
    w.chrome = { runtime: { sendMessage(message, done) { messages.push(structuredClone(message)); done({ created: true, queued: true }); } }, storage: {
      local: { get: (key, done) => done({ siteSettings: { beatport: enabled } }) },
      onChanged: { addListener: listener => { changeSettings = listener; } },
    } };
    const entry = manifest.content_scripts.find(entry => entry.js?.includes('content/beatport.js'));
    w.eval((entry?.js || []).map(file => readFileSync(`${root}/${file}`, 'utf8')).join('\n'));
    return { w, messages, settings: enabled => changeSettings({ siteSettings: { newValue: { beatport: enabled } } }, 'local'),
      close: async () => { await new Promise(resolve => setImmediate(resolve)); w.close(); } };
  }

  test(`${root}: declared track controls preserve artists, mix, parentheses and canonical source`, async () => {
    const f = page('/track/synthetic-recording/910001?ref=fixture',
      'Fixture Alpha, Fixture Beta - Synthetic (Night) (Extended Mix) [Fixture Label] | Music & Downloads on Beatport',
      'Synthetic (Night) Extended Mix');
    try {
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 1);
      f.w.document.querySelector('.ocdj-dig-btn').click();
      f.w.document.querySelector('.ocdj-play-btn').click();
      assert.deepEqual(f.messages.map(m => m.type), ['dig:add', 'dig:queue-embed']);
      assert.deepEqual(f.messages[0].data, {
        artist: 'Fixture Alpha, Fixture Beta', title: 'Synthetic (Night) (Extended Mix)', release_name: '',
        source_site: 'beatport', source_url: 'https://www.beatport.com/track/synthetic-recording/910001',
      });
      assert.equal(f.messages[1].data.title, f.messages[0].data.title);
      assert.equal(f.messages[1].data.source_url, f.messages[0].data.source_url);
      assert.equal(f.messages[1].data.platform, 'beatport');
      assert.equal(f.messages[1].data.embedUrl, 'https://embed.beatport.com/?id=910001&type=track');
    } finally { await f.close(); }
  });

  test(`${root}: localized release is one release-level Wanted and exact preview selector pair`, async () => {
    const f = page('/fr/release/synthetic-release/910002',
      'Fixture Ensemble - Synthetic Release [Fixture Label] | Music & Downloads on Beatport', 'Synthetic Release');
    try {
      f.w.document.querySelector('.ocdj-dig-btn')?.click();
      f.w.document.querySelector('.ocdj-play-btn')?.click();
      assert.equal(f.messages.length, 2);
      assert.equal(f.messages[0].data.title, '');
      assert.equal(f.messages[0].data.release_name, 'Synthetic Release');
      assert.equal(f.messages[0].data.artist, 'Fixture Ensemble');
      assert.equal(f.messages[1].data.title, 'Synthetic Release');
      assert.equal(f.messages[1].data.embedUrl, 'https://embed.beatport.com/?id=910002&type=release');
      assert.equal(f.messages[1].data.source_url, 'https://www.beatport.com/fr/release/synthetic-release/910002');
      f.w.document.body.append(f.w.document.createElement('p'));
      await pause();
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 1);
    } finally { await f.close(); }
  });

  test(`${root}: Queue rejects a stale SPA callback before observer refresh`, async () => {
    const f = page('/track/synthetic/910005', 'Fixture - Synthetic | Beatport', 'Synthetic');
    try {
      const oldButton = f.w.document.querySelector('.ocdj-play-btn');
      f.w.history.pushState({}, '', '/track/new/910006');
      oldButton.click();
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(f.messages.length, 0);
      assert.match(f.w.document.querySelector('.ocdj-toast').textContent, /source changed/);
      assert.equal(oldButton.textContent, 'Queue');
    } finally { await f.close(); }
  });

  test(`${root}: Beatport off setting suppresses both actions and responds to changes`, async () => {
    const f = page('/track/synthetic/910005', 'Fixture - Synthetic | Beatport', 'Synthetic', '', false);
    try {
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 0);
      f.settings(true);
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 1);
      f.settings(false);
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 0);
      assert.equal(f.messages.length, 0);
    } finally { await f.close(); }
  });

  test(`${root}: invalid, foreign or wrong-type canonical metadata exposes no actions`, async () => {
    for (const canonical of ['https://[invalid', 'https://fixture.invalid/track/synthetic/910006',
      'https://www.beatport.com/release/synthetic/910006']) {
      const f = page('/track/synthetic/910006', 'Fixture - Synthetic | Beatport', 'Synthetic', canonical);
      try {
        assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 0);
        assert.equal(f.messages.length, 0);
      } finally { await f.close(); }
    }
  });

  test(`${root}: stale canonical suppresses same-title SPA capture until metadata catches up`, async () => {
    const f = page('/track/synthetic-a/910003', 'Fixture - Synthetic [Label] | Beatport', 'Synthetic',
      'https://www.beatport.com/track/synthetic-a/910003');
    try {
      f.w.history.pushState({}, '', '/track/synthetic-b/910004');
      f.w.document.body.append(f.w.document.createElement('p'));
      await pause();
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 0);
      f.w.document.querySelector('link').href = 'https://www.beatport.com/track/synthetic-b/910004';
      await pause();
      f.w.document.querySelector('.ocdj-dig-btn').click();
      assert.equal(f.messages[0].data.source_url, 'https://www.beatport.com/track/synthetic-b/910004');
      f.w.document.title = 'Fixture Second - Synthetic [Label] | Beatport';
      await pause();
      f.w.document.querySelector('.ocdj-dig-btn').click();
      assert.equal(f.messages[1].data.artist, 'Fixture Second');
      f.w.history.pushState({}, '', '/artist/synthetic/910004');
      f.w.document.body.append(f.w.document.createElement('p'));
      await pause();
      assert.equal(f.w.document.querySelectorAll('.ocdj-btn-group').length, 0);
    } finally { await f.close(); }
  });
}

test('Beatport adapter is byte-identical in connected and Safari release resources', () => {
  assert.equal(readFileSync(`${roots[0]}/content/beatport.js`, 'utf8'), readFileSync(`${roots[1]}/content/beatport.js`, 'utf8'));
  const project = readFileSync('OCDJ Helper/OCDJ Helper.xcodeproj/project.pbxproj', 'utf8');
  assert.match(project, /lastKnownFileType = folder; name = content; path = Resources\/content;/);
  assert.match(project, /content in Resources \*\/ = \{isa = PBXBuildFile/);
});
