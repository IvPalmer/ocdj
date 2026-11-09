// Beatport track/release pages. Queue uses the existing YouTube search workflow;
// the native Beatport embed is not integrated into the OCDJ player/CSP.
(() => {
  'use strict';

  // Public pages may have a language prefix; IDs identify the recording/release.
  const route = path => path.match(/^\/(?:[a-z]{2}\/)?(track|release)\/[^/]+\/(\d+)\/?$/);
  const normalize = text => text.replace(/[()]/g, '').replace(/\s+/g, ' ').trim();
  let metadata = '';
  let group;
  let enabled = false;

  function inject() {
    if (!enabled) return;
    const match = route(window.location.pathname);
    if (!match) return;
    const heading = document.querySelector('h1');
    // Title/canonical may lag behind SPA navigation, including same-named tracks.
    const canonical = document.querySelector('link[rel="canonical"]')?.href || '';
    const currentMetadata = document.title + '|' + canonical;
    if (metadata !== currentMetadata) {
      group?.remove();
      metadata = currentMetadata;
    }
    if (!heading) return;
    if (canonical) {
      let url;
      try { url = new URL(canonical, window.location.href); } catch { return; }
      const canonicalRoute = route(url.pathname);
      if (!['www.beatport.com', 'beatport.com'].includes(url.hostname) ||
          url.protocol !== 'https:' || !canonicalRoute ||
          canonicalRoute[1] !== match[1] || canonicalRoute[2] !== match[2]) return;
    }

    // Beatport's document title includes the primary artists and mix name;
    // global artist links also include charts/recommendations, so don't scrape them.
    const raw = document.title.replace(/\s*\|.*$/, '').replace(/\s*\[[^\]]*\]\s*$/, '').trim();
    const split = raw.indexOf(' - ');
    const artist = split > 0 ? raw.slice(0, split).trim() : '';
    const title = split > 0 ? raw.slice(split + 3).trim() : '';
    // Wait for SPA metadata rather than sending the previous page's recording.
    if (!artist || !title || normalize(title) !== normalize(heading.textContent)) return;
    if (OCDJ.isInjected(heading.parentElement)) return;
    const source_url = window.location.origin + window.location.pathname;
    const thumb = document.querySelector('meta[property="og:image"]')?.content || '';
    const queue = OCDJ.createPlayButton({
      size: 'medium',
      tooltip: 'Find on YouTube and queue for listening',
      onClick: () => OCDJ.sendToBackground('dig:queue-search', {
        platform: 'beatport', artist, title, thumb, source_url,
      }),
    });
    const wantlist = OCDJ.createDigButton({
      size: 'medium',
      onClick: () => OCDJ.sendToBackground('dig:add', {
        artist, title: match[1] === 'track' ? title : '',
        release_name: match[1] === 'release' ? title : '',
        source_site: 'beatport', source_url,
      }),
    });
    group = OCDJ.createButtonGroup(queue, wantlist);
    heading.parentElement.appendChild(group);
  }

  chrome.storage.local.get('siteSettings', ({ siteSettings = {} }) => {
    enabled = siteSettings.beatport !== false;
    OCDJ.observePage(inject);
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !changes.siteSettings) return;
    enabled = changes.siteSettings.newValue?.beatport !== false;
    group?.remove();
    inject();
  });
})();
