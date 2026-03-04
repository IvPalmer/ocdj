// OCDJ Dig — SoundCloud Content Script
// SPA — uses MutationObserver + URL change detection

(() => {
  'use strict';

  let lastUrl = '';

  function init() {
    inject();
    // SoundCloud is a SPA — watch for URL changes
    OCDJ.observeDOM(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        setTimeout(inject, 500);
      }
    });
  }

  function inject() {
    const path = window.location.pathname;

    // Skip non-content pages
    if (path === '/' || path.startsWith('/discover') || path.startsWith('/search') ||
        path.startsWith('/settings') || path.startsWith('/you/')) return;

    if (path.match(/^\/[^/]+\/sets\//)) {
      injectSetPage();
    } else if (path.match(/^\/[^/]+\/[^/]+$/) && !path.includes('/sets')) {
      injectTrackPage();
    }
  }

  // ── Metadata Extraction ─────────────────────────────────────

  function extractMeta() {
    // Uploader name (may not be the actual track artist)
    const uploaderName =
      document.querySelector('.soundTitle__usernameText')?.textContent.trim() ||
      document.querySelector('a[class*="profileHovercard"]')?.textContent.trim() ||
      '';

    // Raw track/set title
    const rawTitle =
      document.querySelector('.soundTitle__title span')?.textContent.trim() ||
      '';

    const ogTitle = document.querySelector('meta[property="og:title"]')?.content || '';
    const titleStr = rawTitle || ogTitle;
    const thumb = document.querySelector('meta[property="og:image"]')?.content || '';

    // Parse "Artist - Title" from the track name
    let artist = uploaderName;
    let title = titleStr;

    const separators = [' - ', ' \u2014 ', ' \u2013 '];
    for (const sep of separators) {
      const idx = titleStr.indexOf(sep);
      if (idx > 0) {
        artist = titleStr.substring(0, idx).trim();
        title = titleStr.substring(idx + sep.length).trim();
        break;
      }
    }

    return { artist, title, uploaderName, thumb };
  }

  // ── Track Page ────────────────────────────────────────────

  function injectTrackPage() {
    const actionsBar =
      document.querySelector('.soundActions .sc-button-group') ||
      document.querySelector('.listenEngagement .soundActions');

    if (!actionsBar || OCDJ.isInjected(actionsBar)) return;

    const { artist, title } = extractMeta();
    // Capture artwork at click time for SPA reliability
    const pageUrl = window.location.href;
    const cleanUrl = pageUrl.split('?')[0]; // strip query params
    const embedUrl = `https://w.soundcloud.com/player/?url=${encodeURIComponent(cleanUrl)}&color=%230d9488&auto_play=true&show_artwork=true`;

    const queueBtn = OCDJ.createPlayButton({
      size: 'medium',
      tooltip: `Queue "${artist} - ${title}" for listening`,
      onClick: () => OCDJ.sendToBackground('dig:queue-embed', {
        platform: 'soundcloud',
        artist,
        title,
        embedUrl,
        thumb: document.querySelector('meta[property="og:image"]')?.content ||
               document.querySelector('.sc-artwork img, img.sc-artwork')?.src || '',
        source_url: pageUrl,
      }),
    });

    const btn = OCDJ.createDigButton({
      size: 'medium',
      tooltip: `Add "${artist} - ${title}" to Wanted List`,
      onClick: () => OCDJ.sendToBackground('dig:add', {
        artist,
        title,
        source_url: pageUrl,
        source_site: 'soundcloud',
      }),
    });

    const group = OCDJ.createButtonGroup(queueBtn, btn);
    group.style.marginLeft = '8px';
    actionsBar.appendChild(group);
  }

  // ── Track List Extraction (Sets) ──────────────────────────

  function extractSetTracks() {
    const tracks = [];
    const rows = document.querySelectorAll(
      '.trackList__item, .soundList__item, ' +
      '.trackItem, .compactTrackList__item, ' +
      '.systemPlaylistTrackList__item'
    );

    rows.forEach((row, i) => {
      const titleEl =
        row.querySelector('.trackItem__trackTitle a') ||
        row.querySelector('.trackItem__trackTitle') ||
        row.querySelector('.compactTrackListItem__trackTitle') ||
        row.querySelector('.soundTitle__title span') ||
        row.querySelector('a[class*="trackTitle"]') ||
        row.querySelector('.trackItem__content a');

      const trackTitle = titleEl?.textContent?.trim() || '';
      if (!trackTitle) return;

      const artistEl =
        row.querySelector('.trackItem__username a') ||
        row.querySelector('.compactTrackListItem__user a') ||
        row.querySelector('a[class*="username"]');

      tracks.push({
        title: trackTitle,
        artist: artistEl?.textContent?.trim() || '',
        track_num: i + 1,
      });
    });

    return tracks;
  }

  // ── Set/Playlist Page ─────────────────────────────────────

  function injectSetPage() {
    const header =
      document.querySelector('.soundActions .sc-button-group') ||
      document.querySelector('.listenEngagement .soundActions');

    if (header && !OCDJ.isInjected(header)) {
      const { artist, title, uploaderName } = extractMeta();
      const pageUrl = window.location.href;
      const embedUrl = `https://w.soundcloud.com/player/?url=${encodeURIComponent(pageUrl)}&color=%230d9488&auto_play=true&show_artwork=true`;

      const queueBtn = OCDJ.createPlayButton({
        size: 'medium',
        tooltip: `Queue "${title}" for listening`,
        onClick: () => OCDJ.sendToBackground('dig:queue-embed', {
          platform: 'soundcloud',
          artist: uploaderName,
          title,
          embedUrl,
          thumb: document.querySelector('meta[property="og:image"]')?.content ||
                 document.querySelector('.sc-artwork img, img.sc-artwork')?.src || '',
          tracks: extractSetTracks(),
          source_url: pageUrl,
        }),
      });

      const btn = OCDJ.createDigButton({
        size: 'medium',
        tooltip: `Add "${title}" to Wanted List`,
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist: uploaderName,
          title,
          source_url: pageUrl,
          source_site: 'soundcloud',
        }),
      });

      const group = OCDJ.createButtonGroup(queueBtn, btn);
      group.style.marginLeft = '8px';
      header.appendChild(group);
    }

    injectSetTrackButtons();
  }

  // ── Per-Track Buttons (Queue + Wantlist) ────────────────────

  function injectSetTrackButtons() {
    const { uploaderName, thumb: setThumb } = extractMeta();

    const rows = document.querySelectorAll(
      '.trackList__item, .soundList__item, ' +
      '.trackItem, .compactTrackList__item, ' +
      '.systemPlaylistTrackList__item'
    );

    rows.forEach((row) => {
      if (OCDJ.isInjected(row)) return;

      const titleLink =
        row.querySelector('.trackItem__trackTitle a') ||
        row.querySelector('a[class*="trackTitle"]') ||
        row.querySelector('.trackItem__content a');

      const titleEl = titleLink ||
        row.querySelector('.trackItem__trackTitle') ||
        row.querySelector('.compactTrackListItem__trackTitle') ||
        row.querySelector('.soundTitle__title span');

      const trackTitle = titleEl?.textContent?.trim() || '';
      if (!trackTitle) return;

      // Parse "Artist - Title" from track title
      let trackArtist = uploaderName;
      let parsedTitle = trackTitle;
      const separators = [' - ', ' \u2014 ', ' \u2013 '];
      for (const sep of separators) {
        const idx = trackTitle.indexOf(sep);
        if (idx > 0) {
          trackArtist = trackTitle.substring(0, idx).trim();
          parsedTitle = trackTitle.substring(idx + sep.length).trim();
          break;
        }
      }

      // Track artwork — look for per-track image, fall back to set artwork
      const trackArt = row.querySelector('img.sc-artwork, img[class*="artwork"]')?.src || '';

      const buttons = [];

      // Queue single track via YouTube search (SC embeds unreliable from set context)
      buttons.push(OCDJ.createPlayButton({
        size: 'small',
        tooltip: `Queue "${trackArtist} - ${parsedTitle}"`,
        onClick: () => OCDJ.sendToBackground('dig:queue-search', {
          platform: 'soundcloud',
          artist: trackArtist,
          title: parsedTitle,
          thumb: trackArt || setThumb,
          source_url: titleLink?.href || window.location.href,
        }),
      }));

      // Wantlist
      buttons.push(OCDJ.createDigButton({
        size: 'small',
        tooltip: `Add "${trackArtist} - ${parsedTitle}" to Wanted List`,
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist: trackArtist,
          title: parsedTitle,
          source_url: titleLink?.href || window.location.href,
          source_site: 'soundcloud',
        }),
      }));

      const group = OCDJ.createButtonGroup(...buttons);

      const contentArea =
        row.querySelector('.trackItem__content') ||
        row.querySelector('.compactTrackListItem__content') ||
        row;
      contentArea.appendChild(group);
    });
  }

  // ── Run ───────────────────────────────────────────────────

  init();
})();
