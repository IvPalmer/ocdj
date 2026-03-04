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

  function injectAlbumTrackPage() {
    const meta = extractMeta();

    const buyArea = document.querySelector('.buyButtons, .tralbumCommands, .inline_player');
    if (buyArea && !OCDJ.isInjected(buyArea)) {
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
        onClick: () => OCDJ.sendToBackground('dig:add', {
          artist: meta.artist || '',
          title: '',
          release_name: meta.release_name || '',
          label: meta.label || '',
          source_url: meta.source_url,
          source_site: 'bandcamp',
        }),
      });
      group.appendChild(btn);

      buyArea.parentElement.insertBefore(group, buyArea.nextSibling);
    }

    // Per-track buttons (Queue + Wantlist)
    if (meta.tracks && meta.tracks.length > 1) {
      const tralbum = extractTralbumData();
      const trackRows = document.querySelectorAll('.track_list .track_row_view, table.track_list tr');
      trackRows.forEach((row, i) => {
        if (OCDJ.isInjected(row)) return;

        const titleEl = row.querySelector('.track-title, .title a, .title span, .title-col span');
        const trackTitle = titleEl?.textContent.trim() || meta.tracks[i]?.title || '';
        if (!trackTitle) return;

        const buttons = [];

        const trackInfo = tralbum?.trackinfo?.[i];
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
          onClick: () => OCDJ.sendToBackground('dig:add', {
            artist: meta.artist || '',
            title: trackTitle,
            release_name: meta.release_name || '',
            label: meta.label || '',
            source_url: meta.source_url,
            source_site: 'bandcamp',
          }),
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
          title: '',
          release_name: title,
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

  init();
})();
