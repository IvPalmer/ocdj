// OCDJ Standalone — SoundCloud Content Script
// SPA — uses MutationObserver + URL change detection
// Per-track queue uses SoundCloud embeds instead of YouTube search

(() => {
  'use strict';

  let lastUrl = '';

  function init() {
    inject();
    OCDJ.observeDOM(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        setTimeout(inject, 500);
      }
    });
  }

  function inject() {
    const path = window.location.pathname;

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
    const uploaderName =
      document.querySelector('.soundTitle__usernameText')?.textContent.trim() ||
      document.querySelector('a[class*="profileHovercard"]')?.textContent.trim() ||
      '';

    const rawTitle =
      document.querySelector('.soundTitle__title span')?.textContent.trim() || '';

    const ogTitle = document.querySelector('meta[property="og:title"]')?.content || '';
    const titleStr = rawTitle || ogTitle;
    const thumb = document.querySelector('meta[property="og:image"]')?.content || '';

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
    const pageUrl = window.location.href;
    const cleanUrl = pageUrl.split('?')[0];
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
      tooltip: `Add "${artist} - ${title}" to Wantlist`,
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
    const seen = new Set();
    const rows = document.querySelectorAll(
      '.trackList__item, .soundList__item, ' +
      '.trackItem, .compactTrackList__item, ' +
      '.systemPlaylistTrackList__item'
    );

    rows.forEach((row) => {
      // Skip hidden/duplicate track list containers
      if (row.offsetParent === null) return;

      const titleEl =
        row.querySelector('.trackItem__trackTitle a') ||
        row.querySelector('.trackItem__trackTitle') ||
        row.querySelector('.compactTrackListItem__trackTitle') ||
        row.querySelector('.soundTitle__title span') ||
        row.querySelector('a[class*="trackTitle"]') ||
        row.querySelector('.trackItem__content a');

      const trackTitle = titleEl?.textContent?.trim() || '';
      if (!trackTitle) return;

      // Dedup by title
      if (seen.has(trackTitle)) return;
      seen.add(trackTitle);

      const artistEl =
        row.querySelector('.trackItem__username a') ||
        row.querySelector('.compactTrackListItem__user a') ||
        row.querySelector('a[class*="username"]');

      tracks.push({
        title: trackTitle,
        artist: artistEl?.textContent?.trim() || '',
        track_num: tracks.length + 1,
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
        tooltip: `Add "${title}" to Wantlist`,
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

  // ── Per-Track Buttons (Queue via SC embed + Wantlist) ─────

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

      const trackArt = row.querySelector('img.sc-artwork, img[class*="artwork"]')?.src || '';
      const trackUrl = titleLink?.href || '';

      const buttons = [];

      // Queue single track via SoundCloud embed (instead of YouTube search)
      if (trackUrl) {
        const cleanTrackUrl = trackUrl.split('?')[0];
        const trackEmbedUrl = `https://w.soundcloud.com/player/?url=${encodeURIComponent(cleanTrackUrl)}&color=%230d9488&auto_play=true&show_artwork=true`;
        buttons.push(OCDJ.createPlayButton({
          size: 'small',
          tooltip: `Queue "${trackArtist} - ${parsedTitle}"`,
          onClick: () => OCDJ.sendToBackground('dig:queue-embed', {
            platform: 'soundcloud',
            artist: trackArtist,
            title: parsedTitle,
            embedUrl: trackEmbedUrl,
            thumb: trackArt || setThumb,
            source_url: trackUrl,
          }),
        }));
      }

      // Wantlist
      buttons.push(OCDJ.createDigButton({
        size: 'small',
        tooltip: `Add "${trackArtist} - ${parsedTitle}" to Wantlist`,
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist: trackArtist,
          title: parsedTitle,
          source_url: trackUrl || window.location.href,
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
