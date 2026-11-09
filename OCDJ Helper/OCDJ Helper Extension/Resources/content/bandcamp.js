// OCDJ Standalone — Bandcamp Content Script
// Injects buttons on album/track/label pages

(() => {
  'use strict';

  function init() {
    const path = window.location.pathname;

    if (path.startsWith('/album/') || path.startsWith('/track/')) {
      injectAlbumTrackPage();
    } else if (path === '/music' || path === '/' || path === '') {
      injectLabelPage();
    }
  }

  // ── Album / Track Pages ───────────────────────────────────

  function extractTralbumData() {
    const el = document.querySelector('[data-tralbum]');
    if (el) {
      try {
        return JSON.parse(el.getAttribute('data-tralbum'));
      } catch (e) { /* ignore */ }
    }
    return null;
  }

  function extractMeta() {
    const meta = { source_site: 'bandcamp', source_url: window.location.href };
    const tralbum = extractTralbumData();

    if (tralbum) {
      meta.artist = tralbum.artist || '';
      meta.release_name = tralbum.current?.title || '';
      meta.release_date = tralbum.current?.release_date || '';
      meta.tracks = (tralbum.trackinfo || []).map(t => ({
        title: t.title || '',
        track_num: t.track_num,
        duration: t.duration,
      }));

      if (tralbum.current?.band_id !== tralbum.current?.selling_band_id) {
        const siteName = document.querySelector('meta[property="og:site_name"]');
        if (siteName) meta.label = siteName.content;
      }
    }

    if (!meta.artist) {
      const ogTitle = document.querySelector('meta[property="og:title"]');
      if (ogTitle) {
        const parts = ogTitle.content.split(', by ');
        if (parts.length === 2) {
          meta.release_name = parts[0].trim();
          meta.artist = parts[1].trim();
        }
      }
    }
    if (!meta.label) {
      const siteName = document.querySelector('meta[property="og:site_name"]');
      if (siteName) meta.label = siteName.content;
    }

    return meta;
  }

  function explicitReleaseFields() {
    const fields = new Map();
    for (const description of document.querySelectorAll('.buyItem .bd, .tralbum-about')) {
      const text = description.cloneNode(true);
      for (const br of text.querySelectorAll('br')) br.replaceWith('\n');
      for (const line of text.textContent.split('\n')) {
        const match = line.match(/^\s*(Catalog(?:ue)?(?: (?:No\.?|Number))?|Release name|Label|Format|Country)\s*:\s*(.+?)\s*$/i);
        if (!match) continue;
        const name = match[1].toLowerCase().replace(/^catalog.*$/, 'catalog');
        const value = match[2].replace(/^\[([^\]]+)\]$/, '$1');
        if (!fields.has(name)) fields.set(name, new Set());
        fields.get(name).add(value);
      }
    }
    return name => fields.get(name)?.size === 1 ? [...fields.get(name)][0] : '';
  }

  function trackPath(value) {
    if (!value) return '';
    try {
      const url = new URL(value, window.location.href);
      return url.origin === window.location.origin && url.pathname.startsWith('/track/')
        ? url.origin + url.pathname : '';
    } catch { return ''; }
  }

  function focusedTrack(tralbum) {
    const info = tralbum?.trackinfo || [];
    if (window.location.pathname.startsWith('/track/')) return info[0];
    const player = document.querySelector('.inline_player .title_link');
    const url = trackPath(player?.getAttribute('href'));
    if (url) return info.find(track => trackPath(track.title_link) === url);
    const title = player?.textContent.trim();
    const matches = title ? info.filter(track => track.title === title) : [];
    return matches.length === 1 ? matches[0] : undefined;
  }

  function wantedPayload(track, fallbackTitle = '', fallbackUrl = '') {
    const meta = extractMeta();
    const tralbum = extractTralbumData();
    const field = explicitReleaseFields();
    const isTrackPage = window.location.pathname.startsWith('/track/');
    const releaseLink = document.querySelector('#name-section a[href*="/album/"]');
    const code = (tralbum?.current?.isrc || '').toUpperCase().replace(/-/g, '');
    const notes = [
      track?.track_num && `Track number: ${track.track_num}`,
      field('format') && `Release format: ${field('format')}`,
      field('country') && `Release country: ${field('country')}`,
    ].filter(Boolean).join('\n');
    return {
      artist: track?.artist || meta.artist || '',
      title: track?.title || fallbackTitle || (isTrackPage ? tralbum?.current?.title || meta.release_name || '' : ''),
      release_name: field('release name') || releaseLink?.textContent.trim() || (isTrackPage ? '' : meta.release_name || ''),
      label: field('label') || (isLabelPage(tralbum) ? meta.label || '' : ''),
      catalog_number: field('catalog'),
      isrc: isTrackPage && /^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$/.test(code) ? code : '',
      notes,
      source_url: trackPath(track?.title_link) || trackPath(fallbackUrl) || meta.source_url,
      source_site: 'bandcamp',
    };
  }

  function isLabelPage(tralbum) {
    const current = tralbum?.current;
    return current?.band_id && current?.selling_band_id && current.band_id !== current.selling_band_id;
  }

  function injectAlbumTrackPage() {
    const meta = extractMeta();

    const buyArea = document.querySelector('.buyButtons, .tralbumCommands, .inline_player');
    // Guard against the place the group actually lands. It is inserted as a
    // *sibling* of buyArea below, while isInjected() searches descendants — so
    // the marker was never where the check looked and every run of this script
    // stacked another Queue/Wantlist pair on the album header. The per-track
    // loop was always fine because its group ends up inside the row it checks.
    const already = buyArea?.parentElement?.querySelector(':scope > .ocdj-btn-group');
    if (buyArea && !already) {
      const group = document.createElement('div');
      group.className = 'ocdj-btn-group';
      group.setAttribute(OCDJ.INJECTED_ATTR, 'true');
      group.style.marginTop = '8px';
      group.style.display = 'flex';

      // Queue button — native Bandcamp embed
      const tralbum = extractTralbumData();
      const tralbumType = tralbum?.current?.type === 'track' ? 'track' : 'album';
      const tralbumId = tralbum?.id || tralbum?.current?.id || '';
      const bcEmbedUrl = tralbumId
        ? `https://bandcamp.com/EmbeddedPlayer/${tralbumType}=${tralbumId}/size=large/bgcol=333333/linkcol=0f91ff/tracklist=true/artwork=small/transparent=true/`
        : '';

      const queueBtn = OCDJ.createPlayButton({
        size: 'large',
        tooltip: `Queue "${meta.artist || ''} - ${meta.release_name || ''}" for listening`,
        onClick: () => {
          if (!bcEmbedUrl) {
            OCDJ.showToast('Could not extract Bandcamp embed info', 'error');
            return Promise.resolve({ error: 'No embed URL' });
          }
          const thumb = document.querySelector('meta[property="og:image"]')?.content ||
                        document.querySelector('#tralbumArt img')?.src || '';
          return OCDJ.sendToBackground('dig:queue-embed', {
            platform: 'bandcamp',
            artist: meta.artist || '',
            title: meta.release_name || '',
            embedUrl: bcEmbedUrl,
            thumb,
            tracks: (meta.tracks || []).map(t => ({ title: t.title || '', track_num: t.track_num })),
            source_url: meta.source_url,
          });
        },
      });
      group.appendChild(queueBtn);

      const btn = OCDJ.createDigButton({
        size: 'large',
        tooltip: 'Add to Wantlist',
        onClick: () => OCDJ.sendToBackground('dig:add', wantedPayload(focusedTrack(extractTralbumData()))),
      });
      group.appendChild(btn);

      buyArea.parentElement.insertBefore(group, buyArea.nextSibling);
    }

    // Per-track buttons (Queue + Wantlist)
    if (meta.tracks && meta.tracks.length > 1) {
      const tralbum = extractTralbumData();
      const trackRows = document.querySelectorAll('.track_list .track_row_view, table.track_list tr');
      trackRows.forEach(row => {
        if (OCDJ.isInjected(row)) return;

        const titleEl = row.querySelector('.track-title, .title a, .title span, .title-col span');
        const trackTitle = titleEl?.textContent.trim();
        if (!trackTitle) return;

        const buttons = [];

        const rowUrl = row.querySelector('a[href*="/track/"]')?.href;
        const exactPath = trackPath(rowUrl);
        const trackInfo = exactPath
          ? tralbum?.trackinfo?.find(track => trackPath(track.title_link) === exactPath)
          : tralbum?.trackinfo?.find(track => track.title === trackTitle);
        const trackId = trackInfo?.id || trackInfo?.track_id;
        if (trackId) {
          const trackEmbedUrl = `https://bandcamp.com/EmbeddedPlayer/track=${trackId}/size=large/bgcol=333333/linkcol=0f91ff/tracklist=false/artwork=small/transparent=true/`;
          buttons.push(OCDJ.createPlayButton({
            size: 'small',
            tooltip: `Queue "${meta.artist} - ${trackTitle}"`,
            onClick: () => {
              const thumb = document.querySelector('meta[property="og:image"]')?.content ||
                            document.querySelector('#tralbumArt img')?.src || '';
              return OCDJ.sendToBackground('dig:queue-embed', {
                platform: 'bandcamp',
                artist: meta.artist || '',
                title: trackTitle,
                embedUrl: trackEmbedUrl,
                thumb,
                source_url: meta.source_url,
              });
            },
          }));
        }

        buttons.push(OCDJ.createDigButton({
          size: 'small',
          tooltip: `Add "${meta.artist} - ${trackTitle}" to Wantlist`,
          onClick: () => OCDJ.sendToBackground('dig:add', wantedPayload(trackInfo, trackTitle, rowUrl)),
        }));

        const group = OCDJ.createButtonGroup(...buttons);

        const titleSpan = row.querySelector('.track-title') ||
                          row.querySelector('.title a') ||
                          row.querySelector('.title span') ||
                          row.querySelector('.title-col span');
        if (titleSpan) {
          titleSpan.parentElement.style.display = 'inline-flex';
          titleSpan.parentElement.style.alignItems = 'center';
          titleSpan.after(group);
        } else {
          const target = row.querySelector('.title-col') || row.querySelector('td:last-child') || row;
          target.appendChild(group);
        }
      });
    }
  }

  // ── Label / Music Pages ───────────────────────────────────

  function injectLabelPage() {
    const albumCards = document.querySelectorAll('.music-grid .music-grid-item, #music-grid li, .editable-grid li');
    albumCards.forEach((card) => {
      if (OCDJ.isInjected(card)) return;

      const linkEl = card.querySelector('a[href*="/album/"], a[href*="/track/"]');
      const titleEl = card.querySelector('.title, .itemText');
      const artistEl = card.querySelector('.artist-override, .subhead');

      if (!linkEl && !titleEl) return;

      const title = titleEl?.textContent.trim() || '';
      const artist = artistEl?.textContent.trim().replace(/^by\s+/i, '') || '';
      const label = document.querySelector('meta[property="og:site_name"]')?.content || '';

      const btn = OCDJ.createDigButton({
        size: 'small',
        tooltip: `Add "${artist ? artist + ' - ' : ''}${title}" to Wantlist`,
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist,
          title: linkEl?.pathname?.startsWith('/track/') ? title : '',
          release_name: linkEl?.pathname?.startsWith('/album/') ? title : '',
          label,
          source_url: linkEl?.href || window.location.href,
          source_site: 'bandcamp',
        }),
      });

      card.style.position = 'relative';
      btn.style.position = 'absolute';
      btn.style.top = '6px';
      btn.style.right = '6px';
      btn.style.opacity = '0';
      btn.style.transition = 'opacity 0.15s';

      card.addEventListener('mouseenter', () => { btn.style.opacity = '1'; });
      card.addEventListener('mouseleave', () => {
        if (!btn.classList.contains('ocdj-dig-btn--added') && !btn.classList.contains('ocdj-dig-btn--duplicate')) {
          btn.style.opacity = '0';
        }
      });

      card.appendChild(btn);
    });
  }

  // ── Run ───────────────────────────────────────────────────

  OCDJ.observePage(init);
})();
