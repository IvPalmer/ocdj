// Beatport track/release pages. Queue uses the existing YouTube search workflow;
// Beatport has no supported native embed in the OCDJ player.
(() => {
  'use strict';

  function inject() {
    const match = window.location.pathname.match(/^\/(track|release)\/[^/]+\/\d+\/?$/);
    if (!match) return;
    const heading = document.querySelector('h1');
    if (!heading || OCDJ.isInjected(heading.parentElement)) return;

    // Beatport's document title includes the primary artists and mix name;
    // global artist links also include charts/recommendations, so don't scrape them.
    const raw = document.title.replace(/\s*\|.*$/, '').replace(/\s*\[[^\]]*\]\s*$/, '').trim();
    const split = raw.indexOf(' - ');
    const artist = split > 0 ? raw.slice(0, split).trim() : '';
    const title = split > 0 ? raw.slice(split + 3).trim() : '';
    // Wait for SPA metadata rather than sending the previous page's recording.
    const headingText = heading.textContent.trim().replace(/\s+/g, ' ');
    const normalizedTitle = title.replace(/[()]/g, '').replace(/\s+/g, ' ');
    if (!artist || !title || normalizedTitle !== headingText) return;
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
    const group = OCDJ.createButtonGroup(queue, wantlist);
    heading.parentElement.appendChild(group);
  }

  OCDJ.observePage(inject);
})();
