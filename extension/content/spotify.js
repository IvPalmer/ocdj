// OCDJ Dig — Spotify Content Script
// SPA — uses MutationObserver + URL detection

(() => {
  'use strict';

  let lastUrl = '';

  function init() {
    inject();
    // Spotify is a SPA — watch for URL changes
    OCDJ.observeDOM(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        setTimeout(inject, 600);
      }
    });
  }

  function inject() {
    const path = window.location.pathname;

    if (path.startsWith('/track/')) {
      injectTrackPage();
    } else if (path.startsWith('/album/')) {
      injectAlbumPage();
    } else if (path.startsWith('/playlist/')) {
      injectPlaylistPage();
    }
  }

  // ── Artwork Extraction (at click time for SPA reliability) ──

  function extractArtwork() {
    // og:image is most reliable but may lag on SPA navigation
    const ogImage = document.querySelector('meta[property="og:image"]')?.content;
    if (ogImage && ogImage.startsWith('http')) return ogImage;

    // Spotify-specific test IDs
    return document.querySelector('[data-testid="cover-art-image"]')?.src ||
           document.querySelector('img[data-testid="entity-image"]')?.src ||
           document.querySelector('.cover-art img')?.src ||
           // Broader fallback — any img in the header area
           document.querySelector('[data-testid="entity-header"] img')?.src ||
           document.querySelector('img[src*="i.scdn.co"]')?.src ||
           '';
  }

  // ── Track Page ────────────────────────────────────────────

  function injectTrackPage() {
    const actionBar =
      document.querySelector('[data-testid="action-bar-row"]') ||
      document.querySelector('.contentSpacing header');

    if (!actionBar || OCDJ.isInjected(actionBar)) return;

    const { artist, title } = extractSpotifyMeta();
    const embedUrl = `https://open.spotify.com/embed${window.location.pathname}?theme=0`;

    const queueBtn = OCDJ.createPlayButton({
      size: 'medium',
      tooltip: `Queue "${artist} - ${title}" for listening`,
      onClick: () => OCDJ.sendToBackground('dig:queue-embed', {
        platform: 'spotify',
        artist,
        title,
        embedUrl,
        thumb: extractArtwork(), // capture at click time
        source_url: window.location.href,
      }),
    });

    const btn = OCDJ.createDigButton({
      size: 'medium',
      tooltip: `Add "${artist} - ${title}" to Wanted List`,
      onClick: () => OCDJ.sendToBackground('dig:add', {
        artist,
        title,
        source_url: window.location.href,
        source_site: 'spotify',
      }),
    });

    const group = OCDJ.createButtonGroup(queueBtn, btn);
    group.style.marginLeft = '12px';
    actionBar.appendChild(group);
  }

  // ── Track List Extraction ─────────────────────────────────

  function extractSpotifyTracks() {
    const tracks = [];
    const rows = document.querySelectorAll(
      '[data-testid="tracklist-row"], ' +
      '[data-testid="playlist-tracklist"] [role="row"], ' +
      '.tracklist-row'
    );

    rows.forEach((row, i) => {
      // Track name — try multiple selectors for robustness
      const nameEl =
        row.querySelector('[data-testid="internal-track-link"] div') ||
        row.querySelector('a[data-testid="internal-track-link"]') ||
        row.querySelector('.tracklist-name') ||
        row.querySelector('[class*="TrackName"]') ||
        row.querySelector('a[href*="/track/"] div');

      const trackName = nameEl?.textContent?.trim() || '';
      if (!trackName) return;

      // Track artist (may differ from album artist)
      const artistEls = row.querySelectorAll(
        'a[href*="/artist/"], [data-testid="artists-link"] a'
      );
      const trackArtist = artistEls.length > 0
        ? Array.from(artistEls).map(a => a.textContent.trim()).join(', ')
        : '';

      tracks.push({
        title: trackName,
        artist: trackArtist,
        track_num: i + 1,
      });
    });

    return tracks;
  }

  // ── Album Page ────────────────────────────────────────────

  function injectAlbumPage() {
    const actionBar =
      document.querySelector('[data-testid="action-bar-row"]') ||
      document.querySelector('.contentSpacing header');

    if (actionBar && !OCDJ.isInjected(actionBar)) {
      const { artist, title } = extractSpotifyMeta();
      const embedUrl = `https://open.spotify.com/embed${window.location.pathname}?theme=0`;

      const queueBtn = OCDJ.createPlayButton({
        size: 'medium',
        tooltip: `Queue "${artist} - ${title}" for listening`,
        onClick: () => OCDJ.sendToBackground('dig:queue-embed', {
          platform: 'spotify',
          artist,
          title,
          embedUrl,
          thumb: extractArtwork(),
          tracks: extractSpotifyTracks(),
          source_url: window.location.href,
        }),
      });

      const btn = OCDJ.createDigButton({
        size: 'medium',
        tooltip: `Add album "${title}" to Wanted List`,
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist,
          title: '',
          release_name: title,
          source_url: window.location.href,
          source_site: 'spotify',
        }),
      });

      const group = OCDJ.createButtonGroup(queueBtn, btn);
      group.style.marginLeft = '12px';
      actionBar.appendChild(group);
    }

    injectTracklistButtons();
  }

  // ── Playlist Page ─────────────────────────────────────────

  function injectPlaylistPage() {
    const actionBar =
      document.querySelector('[data-testid="action-bar-row"]') ||
      document.querySelector('.contentSpacing header');

    if (actionBar && !OCDJ.isInjected(actionBar)) {
      const { artist, title } = extractSpotifyMeta();
      const embedUrl = `https://open.spotify.com/embed${window.location.pathname}?theme=0`;

      const queueBtn = OCDJ.createPlayButton({
        size: 'medium',
        tooltip: `Queue "${title}" for listening`,
        onClick: () => OCDJ.sendToBackground('dig:queue-embed', {
          platform: 'spotify',
          artist,
          title,
          embedUrl,
          thumb: extractArtwork(),
          tracks: extractSpotifyTracks(),
          source_url: window.location.href,
        }),
      });

      const btn = OCDJ.createDigButton({
        size: 'medium',
        tooltip: `Add "${title}" to Wanted List`,
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist,
          title,
          source_url: window.location.href,
          source_site: 'spotify',
        }),
      });

      const group = OCDJ.createButtonGroup(queueBtn, btn);
      group.style.marginLeft = '12px';
      actionBar.appendChild(group);
    }

    injectTracklistButtons();
  }

  // ── Per-Track Buttons (Queue + Wantlist) ───────────────────

  function injectTracklistButtons() {
    const { artist: albumArtist } = extractSpotifyMeta();

    const rows = document.querySelectorAll(
      '[data-testid="tracklist-row"], ' +
      '[data-testid="playlist-tracklist"] [role="row"], ' +
      '.tracklist-row'
    );

    rows.forEach((row) => {
      if (OCDJ.isInjected(row)) return;

      // Track link — need href to build single-track embed URL
      const trackLink =
        row.querySelector('a[data-testid="internal-track-link"]') ||
        row.querySelector('a[href*="/track/"]');

      const trackHref = trackLink?.getAttribute('href') || '';

      // Track name
      const nameEl =
        (trackLink && trackLink.querySelector('div')) ||
        trackLink ||
        row.querySelector('.tracklist-name') ||
        row.querySelector('[class*="TrackName"]');

      const trackName = nameEl?.textContent?.trim() || '';
      if (!trackName) return;

      // Track artist
      const artistEls = row.querySelectorAll(
        'a[href*="/artist/"], [data-testid="artists-link"] a'
      );
      const trackArtist = artistEls.length > 0
        ? Array.from(artistEls).map(a => a.textContent.trim()).join(', ')
        : albumArtist;

      const buttons = [];

      // Queue single track
      if (trackHref) {
        const trackPath = trackHref.startsWith('http')
          ? new URL(trackHref).pathname
          : trackHref;
        const embedUrl = `https://open.spotify.com/embed${trackPath}?theme=0`;

        buttons.push(OCDJ.createPlayButton({
          size: 'small',
          tooltip: `Queue "${trackArtist} - ${trackName}"`,
          onClick: () => OCDJ.sendToBackground('dig:queue-embed', {
            platform: 'spotify',
            artist: trackArtist,
            title: trackName,
            embedUrl,
            thumb: extractArtwork(),
            source_url: `https://open.spotify.com${trackPath}`,
          }),
        }));
      }

      // Wantlist
      buttons.push(OCDJ.createDigButton({
        size: 'small',
        tooltip: `Add "${trackArtist} - ${trackName}" to Wanted List`,
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist: trackArtist,
          title: trackName,
          source_url: window.location.href,
          source_site: 'spotify',
        }),
      }));

      const group = OCDJ.createButtonGroup(...buttons);

      // Insert inside the track name column, not as a grid sibling
      const nameColumn =
        row.querySelector('[data-testid="tracklist-row"] > div:nth-child(2)') ||
        (trackLink && trackLink.closest('div[class]')) ||
        row;

      // Find the innermost container that holds the title
      const titleContainer = nameEl?.parentElement || nameColumn;
      titleContainer.style.display = 'flex';
      titleContainer.style.alignItems = 'center';
      titleContainer.style.gap = '0';
      titleContainer.appendChild(group);
    });
  }

  // ── Metadata Extraction ───────────────────────────────────

  function extractSpotifyMeta() {
    const ogTitle = document.querySelector('meta[property="og:title"]')?.content || '';
    const ogDesc = document.querySelector('meta[property="og:description"]')?.content || '';

    let artist = '';
    let title = '';

    // og:description often contains "Song · Artist · Album · 2024"
    if (ogDesc) {
      const parts = ogDesc.split(' \u00B7 ');
      if (parts.length >= 2) {
        const descMatch = ogDesc.match(/^(?:Listen to .+ on Spotify\.\s*)?(.+?)(?:\s*·|$)/);
        if (descMatch) {
          if (parts.length >= 2) {
            artist = parts[parts.length >= 3 ? 1 : 0].trim();
          }
        }
      }
    }

    // From og:title
    if (ogTitle) {
      title = ogTitle
        .replace(/\s*[-|]\s*Spotify\s*$/, '')
        .replace(/\s*[-\u2013]\s*(song|album)\s*(and lyrics\s*)?by\s+.+$/i, '')
        .trim();

      const byMatch = ogTitle.match(/(?:song|album)\s+(?:and lyrics\s+)?by\s+(.+?)(?:\s*[-|]\s*Spotify)?$/i);
      if (byMatch) {
        artist = byMatch[1].trim();
      }
    }

    // DOM fallback
    if (!title) {
      const titleEl = document.querySelector('[data-testid="entityTitle"] h1') ||
                       document.querySelector('h1');
      title = titleEl?.textContent.trim() || '';
    }
    if (!artist) {
      const artistEl = document.querySelector('[data-testid="creator-link"]') ||
                        document.querySelector('a[href*="/artist/"]');
      artist = artistEl?.textContent.trim() || '';
    }

    return { artist, title };
  }

  // ── Run ───────────────────────────────────────────────────

  init();
})();
