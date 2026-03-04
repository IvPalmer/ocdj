// OCDJ Dig — Discogs Content Script
// Injects "Dig" buttons on release, label, marketplace, and search pages

(() => {
  'use strict';

  function init() {
    const path = window.location.pathname;

    if (path.match(/^\/release\//) || path.match(/^\/master\//)) {
      injectReleasePage();
    } else if (path.match(/^\/label\//) || path.match(/^\/artist\//)) {
      injectListPage();
    } else if (path.match(/^\/sell\//) || path.match(/^\/seller\//)) {
      injectMarketplacePage();
    } else if (path.match(/^\/search/)) {
      injectSearchPage();
    }
  }

  // ── Release ID Extraction ────────────────────────────────

  function extractReleaseId(url) {
    const m = (url || window.location.pathname).match(/\/release\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
  }

  // ── Release Page Metadata ─────────────────────────────────

  function extractReleaseMetadata() {
    const meta = { source_site: 'discogs', source_url: window.location.href };

    // 1. JSON-LD (most reliable structured data)
    for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(el.textContent);
        if (data['@type'] === 'MusicAlbum' || data['@type'] === 'MusicRelease') {
          if (data.name) meta.release_name = data.name;
          if (data.byArtist) {
            const raw = Array.isArray(data.byArtist)
              ? data.byArtist.map(a => a.name).filter(Boolean).join(', ')
              : data.byArtist.name || '';
            if (raw) meta.artist = cleanArtist(raw);
          }
          if (data.recordLabel) {
            const lab = Array.isArray(data.recordLabel) ? data.recordLabel[0] : data.recordLabel;
            if (lab?.name) meta.label = lab.name;
          }
          if (data.catalogNumber) meta.catalog_number = data.catalogNumber;
        }
      } catch (e) { /* ignore */ }
    }

    // 2. Page title: "Tatsuro Yamashita = 山下達郎* – For You | Discogs"
    const pageTitle = document.title.replace(/\s*[|\-]\s*Discogs\s*$/i, '').trim();
    const titleMatch = pageTitle.match(/^(.+?)\s*[–\-]\s*(.+)$/);
    if (titleMatch) {
      if (!meta.artist) meta.artist = cleanArtist(titleMatch[1]);
      if (!meta.release_name) meta.release_name = titleMatch[2].trim();
    }

    // 3. DOM: artist links in the header/title area
    if (!meta.artist) {
      const headerArea = document.querySelector('#profile_title, h1, [class*="profile_title"]');
      if (headerArea) {
        const artistLinks = headerArea.querySelectorAll('a[href*="/artist/"]');
        if (artistLinks.length > 0) {
          meta.artist = cleanArtist(
            Array.from(artistLinks).map(a => a.textContent.trim()).join(', ')
          );
        }
      }
    }

    // 4. Label + Cat# from profile info or any info table
    if (!meta.label || !meta.catalog_number) {
      extractProfileInfo(meta);
    }

    return meta;
  }

  function cleanArtist(raw) {
    return raw
      .replace(/\s*=\s*.+$/, '')
      .replace(/\*+/g, '')
      .replace(/\s*\(\d+\)\s*/g, '')
      .trim();
  }

  function extractProfileInfo(meta) {
    const body = document.body.innerText || '';

    if (!meta.label) {
      const labelLinks = document.querySelectorAll('a[href*="/label/"]');
      for (const link of labelLinks) {
        if (link.closest('nav, footer, #header, .header')) continue;
        const text = link.textContent.trim();
        if (text && text.length > 1 && text.length < 100) {
          meta.label = text;
          break;
        }
      }
    }

    if (!meta.catalog_number) {
      const catMatch = body.match(/Cat#\s*[:\s]*\n?\s*([A-Z0-9][\w\s\-./,]*)/i);
      if (catMatch) {
        meta.catalog_number = catMatch[1].trim().split('\n')[0].trim();
      }
    }
  }

  // ── Helper: create inline button group ──────────────────

  function createButtonGroup(...buttons) {
    const group = document.createElement('span');
    group.className = 'ocdj-btn-group';
    group.setAttribute(OCDJ.INJECTED_ATTR, 'true');
    buttons.forEach(b => group.appendChild(b));
    return group;
  }

  // ── Release Page ──────────────────────────────────────────

  function injectReleasePage() {
    const meta = extractReleaseMetadata();
    const releaseId = extractReleaseId();

    // Inject ▶ queue + Dig buttons in the release header area
    injectReleaseHeader(meta, releaseId);

    // Per-track dig buttons inline in each row
    injectTracklistButtons(meta, releaseId);
  }

  function injectReleaseHeader(meta, releaseId) {
    // Find the release title heading — Discogs uses h1 or profile_title
    const titleEl =
      document.querySelector('[class*="title_"] h1') ||
      document.querySelector('#profile_title h1') ||
      document.querySelector('h1[class*="title"]') ||
      document.querySelector('h1');

    if (!titleEl || OCDJ.isInjected(titleEl)) return;

    const buttons = [];

    // Queue release button
    if (releaseId) {
      buttons.push(OCDJ.createPlayButton({
        size: 'medium',
        tooltip: `Queue "${meta.artist || ''} - ${meta.release_name || ''}" for listening`,
        onClick: () => OCDJ.sendToBackground('dig:queue', {
          releaseId,
          artist: meta.artist || '',
          title: meta.release_name || '',
          source_url: meta.source_url,
        }),
      }));
    }

    // Wantlist button
    buttons.push(OCDJ.createDigButton({
      size: 'medium',
      tooltip: `Add "${meta.artist || ''} - ${meta.release_name || ''}" to Wanted List`,
      onClick: () => OCDJ.sendToBackground('dig:add', {
        artist: meta.artist || '',
        title: '',
        release_name: meta.release_name || '',
        catalog_number: meta.catalog_number || '',
        label: meta.label || '',
        source_url: meta.source_url,
        source_site: 'discogs',
      }),
    }));

    if (buttons.length > 0) {
      titleEl.appendChild(createButtonGroup(...buttons));
    }
  }

  // ── Tracklist Buttons ─────────────────────────────────────

  function injectTracklistButtons(meta, releaseId) {
    let rows = document.querySelectorAll('table[class*="tracklist"] tr[data-track-position]');
    if (rows.length === 0) rows = document.querySelectorAll('tr[data-track-position]');
    if (rows.length === 0) {
      const table = document.querySelector('table[class*="tracklist"]');
      if (table) rows = table.querySelectorAll('tr:not([class*="heading"])');
    }

    if (!rows || rows.length === 0) return;

    rows.forEach((row) => {
      if (OCDJ.isInjected(row)) return;
      if (row.className && row.className.includes('heading')) return;

      const posEl = row.querySelector('td[class*="trackPos"]');
      const posText = posEl ? posEl.textContent.trim() : '';
      if (!posText || posText.length > 6) return;

      const titleCell = row.querySelector('td[class*="trackTitle"]');
      let trackTitle = '';
      if (titleCell) {
        const innerTitle = titleCell.querySelector('span[class*="trackTitle"]');
        if (innerTitle) {
          trackTitle = innerTitle.textContent.trim();
        } else {
          const firstSpan = titleCell.querySelector('span');
          trackTitle = firstSpan ? firstSpan.textContent.trim() : '';
        }
      }
      if (!trackTitle) trackTitle = extractTrackTitle(row);
      if (!trackTitle) return;

      const trackArtistEl = row.querySelector('td[class*="artist"] a[href*="/artist/"], a[href*="/artist/"]');
      const trackArtist = trackArtistEl
        ? cleanArtist(trackArtistEl.textContent.trim())
        : (meta.artist || '');

      // Insert buttons inline into the title cell
      const target = titleCell || row.querySelector('td[class*="trackTitle"]') || row;

      const buttons = [];

      // Queue single track (searches YouTube for artist + title)
      buttons.push(OCDJ.createPlayButton({
        size: 'small',
        tooltip: `Queue "${trackArtist} - ${trackTitle}"`,
        onClick: () => OCDJ.sendToBackground('dig:queue-search', {
          artist: trackArtist,
          title: trackTitle,
          source_url: meta.source_url,
        }),
      }));

      buttons.push(OCDJ.createDigButton({
        size: 'small',
        tooltip: `Add "${trackArtist} - ${trackTitle}"`,
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist: trackArtist,
          title: trackTitle,
          release_name: meta.release_name || '',
          catalog_number: meta.catalog_number || '',
          label: meta.label || '',
          source_url: meta.source_url,
          source_site: 'discogs',
        }),
      }));

      const group = OCDJ.createButtonGroup(...buttons);
      target.appendChild(group);
    });
  }

  function extractTrackTitle(row) {
    const titleEl = row.querySelector(
      'span[class*="trackTitle"], ' +
      'td[class*="trackTitle"] > span:first-child, ' +
      '[itemprop="name"]'
    );
    if (titleEl) {
      const text = titleEl.textContent.trim();
      if (text && text.length > 0) return text;
    }

    const cells = row.querySelectorAll('td');
    if (cells.length >= 2) {
      for (let i = 0; i < cells.length; i++) {
        const cell = cells[i];
        const text = cell.textContent.trim();
        if (text.match(/^[A-Z]?\d{1,2}$/)) continue;
        if (text.match(/^\d{1,2}:\d{2}$/)) continue;
        if (!text) continue;
        return extractFirstLine(cell);
      }
    }

    return extractFirstLine(row);
  }

  function extractFirstLine(el) {
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        const text = child.textContent.trim();
        if (text && text.length > 1) return text;
      }
      if (child.nodeType === Node.ELEMENT_NODE) {
        const tag = child.tagName.toLowerCase();
        if (tag === 'ul' || tag === 'dl' || child.querySelector('ul, dl')) continue;
        if (child.classList && (
          child.className.includes('credit') ||
          child.className.includes('extra') ||
          child.className.includes('sub')
        )) continue;
        if (tag === 'span' || tag === 'a') {
          const text = child.textContent.trim();
          if (text && text.length > 1 && text.length < 200) return text;
        }
      }
    }
    const firstLine = el.innerText?.split('\n')[0]?.trim();
    if (firstLine && firstLine.length > 1 && firstLine.length < 200) return firstLine;
    return null;
  }

  // ── Label / Artist Discography Pages ──────────────────────

  function injectListPage() {
    const rows = document.querySelectorAll(
      '#discography .card, table.cards tr, .discography tr, ' +
      '[class*="discography"] tr, [class*="Discography"] [class*="row"], ' +
      'table tbody tr'
    );

    rows.forEach((row) => {
      if (OCDJ.isInjected(row)) return;

      const titleEl = row.querySelector('a[href*="/release/"], a[href*="/master/"]');
      const artistEl = row.querySelector('a[href*="/artist/"]');
      if (!titleEl) return;

      let catno = '';
      for (const cell of row.querySelectorAll('td')) {
        const text = cell.textContent.trim();
        if (text.match(/^[A-Z]{2,}[\s-]?\d+/)) { catno = text; break; }
      }

      const buttons = [];

      const rowReleaseId = extractReleaseId(titleEl.href);
      if (rowReleaseId) {
        buttons.push(OCDJ.createPlayButton({
          size: 'small',
          tooltip: 'Queue for listening',
          onClick: () => OCDJ.sendToBackground('dig:queue', {
            releaseId: rowReleaseId,
            artist: artistEl ? cleanArtist(artistEl.textContent.trim()) : '',
            title: titleEl.textContent.trim(),
            source_url: titleEl.href,
          }),
        }));
      }

      buttons.push(OCDJ.createDigButton({
        size: 'small',
        tooltip: 'Add to Wanted List',
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist: artistEl ? cleanArtist(artistEl.textContent.trim()) : '',
          title: '',
          release_name: titleEl.textContent.trim(),
          catalog_number: catno,
          source_url: titleEl.href || window.location.href,
          source_site: 'discogs',
        }),
      }));

      // Insert inline next to the title link
      titleEl.parentElement.appendChild(createButtonGroup(...buttons));
    });
  }

  // ── Marketplace / Seller Pages ────────────────────────────

  function injectMarketplacePage() {
    const items = document.querySelectorAll(
      '.shortcut_navigable, .table_block tr, .mpitems .item, table tbody tr'
    );

    items.forEach((item) => {
      if (OCDJ.isInjected(item)) return;

      const titleEl = item.querySelector('a[href*="/release/"], a[href*="/sell/item/"]');
      const artistEl = item.querySelector('a[href*="/artist/"]');
      if (!titleEl) return;

      const buttons = [];

      const mktReleaseId = extractReleaseId(titleEl.href);
      if (mktReleaseId) {
        buttons.push(OCDJ.createPlayButton({
          size: 'small',
          tooltip: 'Queue for listening',
          onClick: () => OCDJ.sendToBackground('dig:queue', {
            releaseId: mktReleaseId,
            artist: artistEl ? cleanArtist(artistEl.textContent.trim()) : '',
            title: titleEl.textContent.trim(),
            source_url: titleEl.href,
          }),
        }));
      }

      buttons.push(OCDJ.createDigButton({
        size: 'small',
        tooltip: 'Add to Wanted List',
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist: artistEl ? cleanArtist(artistEl.textContent.trim()) : '',
          release_name: titleEl.textContent.trim(),
          title: '',
          source_url: titleEl.href || window.location.href,
          source_site: 'discogs',
        }),
      }));

      titleEl.parentElement.appendChild(createButtonGroup(...buttons));
    });
  }

  // ── Search Results ────────────────────────────────────────

  function injectSearchPage() {
    const cards = document.querySelectorAll(
      '.card, #search_results .shortcut_navigable, [class*="search_result"]'
    );

    cards.forEach((card) => {
      if (OCDJ.isInjected(card)) return;

      const titleEl = card.querySelector('a[href*="/release/"], a[href*="/master/"]');
      const artistEl = card.querySelector('a[href*="/artist/"]');
      const labelEl = card.querySelector('a[href*="/label/"]');
      if (!titleEl) return;

      const buttons = [];

      const searchReleaseId = extractReleaseId(titleEl.href);
      if (searchReleaseId) {
        buttons.push(OCDJ.createPlayButton({
          size: 'small',
          tooltip: 'Queue for listening',
          onClick: () => OCDJ.sendToBackground('dig:queue', {
            releaseId: searchReleaseId,
            artist: artistEl ? cleanArtist(artistEl.textContent.trim()) : '',
            title: titleEl.textContent.trim(),
            source_url: titleEl.href,
          }),
        }));
      }

      buttons.push(OCDJ.createDigButton({
        size: 'small',
        tooltip: 'Add to Wanted List',
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist: artistEl ? cleanArtist(artistEl.textContent.trim()) : '',
          release_name: titleEl.textContent.trim(),
          title: '',
          label: labelEl?.textContent.trim() || '',
          source_url: titleEl.href || window.location.href,
          source_site: 'discogs',
        }),
      }));

      titleEl.parentElement.appendChild(createButtonGroup(...buttons));
    });
  }

  // ── Run ───────────────────────────────────────────────────

  init();

  let retryTimer = null;
  OCDJ.observeDOM(() => {
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = setTimeout(() => init(), 300);
  });
})();
