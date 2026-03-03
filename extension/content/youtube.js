// OCDJ Dig — YouTube Content Script
// SPA — uses yt-navigate-finish to re-inject on page changes

(() => {
  'use strict';

  function init() {
    inject();
    // YouTube is a SPA — re-inject on navigation
    document.addEventListener('yt-navigate-finish', () => {
      setTimeout(inject, 500);
    });
  }

  function inject() {
    const path = window.location.pathname;

    if (path === '/watch') {
      injectVideoPage();
    } else if (path === '/playlist') {
      injectPlaylistPage();
    }
  }

  // ── Video Page ────────────────────────────────────────────

  function injectVideoPage() {
    const actionsBar =
      document.querySelector('#actions #actions-inner') ||
      document.querySelector('#top-level-buttons-computed') ||
      document.querySelector('ytd-menu-renderer.ytd-watch-metadata');

    if (!actionsBar || OCDJ.isInjected(actionsBar)) return;

    // Extract title from page
    const videoTitle =
      document.querySelector('h1.ytd-watch-metadata yt-formatted-string')?.textContent ||
      document.querySelector('h1.title')?.textContent ||
      document.title.replace(/ - YouTube$/, '') ||
      '';

    const parsed = OCDJ.parseVideoTitle(videoTitle);

    // Extract video ID for queue
    const urlParams = new URLSearchParams(window.location.search);
    const videoId = urlParams.get('v');

    // Build button group
    const buttons = [];

    // Queue button
    if (videoId) {
      buttons.push(OCDJ.createPlayButton({
        size: 'medium',
        tooltip: 'Queue for listening',
        onClick: () => OCDJ.sendToBackground('dig:queue-yt', {
          videoId,
          artist: parsed.artist,
          title: parsed.title || videoTitle,
          source_url: window.location.href,
        }),
      }));
    }

    // Wantlist button
    buttons.push(OCDJ.createDigButton({
      size: 'medium',
      tooltip: parsed.artist
        ? `Add "${parsed.artist} - ${parsed.title}" to Wanted List`
        : `Add "${parsed.title}" to Wanted List`,
      onClick: () => OCDJ.sendToBackground('dig:add', {
        artist: parsed.artist,
        title: parsed.title,
        notes: parsed.raw_title,
        source_url: window.location.href,
        source_site: 'youtube',
      }),
    }));

    // "Recognize" button for long videos (>15 min)
    const durationEl = document.querySelector('.ytp-time-duration');
    if (durationEl) {
      const parts = durationEl.textContent.split(':').map(Number);
      const totalSeconds = parts.length === 3
        ? parts[0] * 3600 + parts[1] * 60 + parts[2]
        : parts[0] * 60 + parts[1];

      if (totalSeconds > 900) { // > 15 minutes
        const recBtn = document.createElement('button');
        recBtn.className = 'ocdj-import-btn';
        recBtn.setAttribute(OCDJ.INJECTED_ATTR, 'true');
        recBtn.textContent = 'Recognize';
        recBtn.title = 'Send to OCDJ Recognize — identify tracks in this mix';

        recBtn.addEventListener('click', async (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (recBtn.classList.contains('ocdj-import-btn--loading')) return;

          recBtn.classList.add('ocdj-import-btn--loading');
          recBtn.textContent = 'Sending...';

          try {
            await OCDJ.sendToBackground('dig:recognize', {
              url: window.location.href,
            });
            recBtn.classList.remove('ocdj-import-btn--loading');
            recBtn.classList.add('ocdj-import-btn--done');
            recBtn.textContent = 'Sent!';
            OCDJ.showToast('Mix sent to Recognize pipeline', 'success');
          } catch (err) {
            recBtn.classList.remove('ocdj-import-btn--loading');
            recBtn.textContent = 'Recognize';
            OCDJ.showToast(err.message || 'Failed to send', 'error');
          }
        });

        buttons.push(recBtn);
      }
    }

    // Wrap all buttons in a group to prevent YouTube's CSS from stretching them
    const group = OCDJ.createButtonGroup(...buttons);
    group.style.marginLeft = '8px';
    actionsBar.appendChild(group);
  }

  // ── Playlist Page ─────────────────────────────────────────

  function injectPlaylistPage() {
    const header =
      document.querySelector('ytd-playlist-header-renderer .metadata-action-bar') ||
      document.querySelector('#header-container .metadata-buttons-wrapper') ||
      document.querySelector('ytd-playlist-header-renderer');

    if (!header || OCDJ.isInjected(header)) return;

    const url = window.location.href;
    const playlistTitle =
      document.querySelector('yt-formatted-string.ytd-playlist-header-renderer')?.textContent.trim() ||
      document.querySelector('h1.ytd-playlist-header-renderer')?.textContent.trim() ||
      document.title.replace(/ - YouTube$/, '') ||
      '';
    const channelName =
      document.querySelector('#owner-text a')?.textContent.trim() ||
      document.querySelector('ytd-playlist-header-renderer [class*="owner"] a')?.textContent.trim() ||
      '';
    const thumb =
      document.querySelector('ytd-playlist-thumbnail img')?.src ||
      '';

    // Extract playlist ID
    const listId = new URLSearchParams(window.location.search).get('list') || '';

    const buttons = [];

    // Queue button — embed the playlist via backend proxy
    if (listId) {
      const embedUrl = `https://www.youtube-nocookie.com/embed/videoseries?list=${listId}&autoplay=1`;
      buttons.push(OCDJ.createPlayButton({
        size: 'medium',
        tooltip: `Queue "${playlistTitle}" for listening`,
        onClick: () => OCDJ.sendToBackground('dig:queue-embed', {
          platform: 'youtube',
          artist: channelName,
          title: playlistTitle,
          embedUrl,
          thumb,
          source_url: url,
        }),
      }));
    }

    // Wantlist button
    buttons.push(OCDJ.createDigButton({
      size: 'medium',
      tooltip: `Add "${playlistTitle}" to Wanted List`,
      onClick: () => OCDJ.sendToBackground('dig:add', {
        artist: channelName,
        title: playlistTitle,
        source_url: url,
        source_site: 'youtube',
      }),
    }));

    const group = OCDJ.createButtonGroup(...buttons);
    group.style.marginLeft = '12px';
    header.appendChild(group);
  }

  // ── Run ───────────────────────────────────────────────────

  init();
})();
