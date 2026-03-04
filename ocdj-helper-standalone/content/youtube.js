// OCDJ Standalone — YouTube Content Script
// SPA — uses yt-navigate-finish to re-inject on page changes
// Removed: Recognize button, Import playlist button (need backend)

(() => {
  'use strict';

  function init() {
    inject();
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

    const videoTitle =
      document.querySelector('h1.ytd-watch-metadata yt-formatted-string')?.textContent ||
      document.querySelector('h1.title')?.textContent ||
      document.title.replace(/ - YouTube$/, '') ||
      '';

    const parsed = OCDJ.parseVideoTitle(videoTitle);

    const urlParams = new URLSearchParams(window.location.search);
    const videoId = urlParams.get('v');

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
        ? `Add "${parsed.artist} - ${parsed.title}" to Wantlist`
        : `Add "${parsed.title}" to Wantlist`,
      onClick: () => OCDJ.sendToBackground('dig:add', {
        artist: parsed.artist,
        title: parsed.title,
        notes: parsed.raw_title,
        source_url: window.location.href,
        source_site: 'youtube',
      }),
    }));

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
      document.querySelector('ytd-playlist-thumbnail img')?.src || '';

    const listId = new URLSearchParams(window.location.search).get('list') || '';

    const buttons = [];

    // Queue button — embed the playlist directly
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
      tooltip: `Add "${playlistTitle}" to Wantlist`,
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
