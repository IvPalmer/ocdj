// OCDJ Standalone — Side Panel Player
// Direct embeds: YouTube, Bandcamp, SoundCloud, Spotify — no backend proxy

(() => {
  'use strict';

  // ── State ──────────────────────────────────────────────────

  let queue = [];
  let currentReleaseIndex = 0;
  let currentVideoIndex = 0;
  let isPlaying = false;
  let port = null;
  let fallbackTimer = null;

  // ── DOM Refs ───────────────────────────────────────────────

  const iframe = document.getElementById('embed-player');
  const playerContainer = document.getElementById('player-container');
  const placeholder = document.getElementById('player-placeholder');
  const nowPlayingTitle = document.getElementById('now-playing-title');
  const nowPlayingRelease = document.getElementById('now-playing-release');
  const btnPrev = document.getElementById('btn-prev');
  const btnNext = document.getElementById('btn-next');
  const btnNextRelease = document.getElementById('btn-next-release');
  const btnClearQueue = document.getElementById('btn-clear-queue');
  const queueList = document.getElementById('queue-list');
  const queueEmpty = document.getElementById('queue-empty');
  const queueCount = document.getElementById('queue-count');
  const embedFallback = document.getElementById('embed-fallback');
  const embedFallbackLink = document.getElementById('embed-fallback-link');

  // ── Platform Helpers ───────────────────────────────────────

  function hasEmbed(release) {
    return !!release.embedUrl;
  }

  function getReleasePlatform(release) {
    return release.platform || 'discogs';
  }

  function setPlayerContainerClass(release) {
    playerContainer.className = '';
    const platform = getReleasePlatform(release);

    if (platform === 'bandcamp') {
      playerContainer.classList.add('platform-bandcamp');
    } else if (platform === 'soundcloud') {
      const isSet = release.source_url && release.source_url.includes('/sets/');
      playerContainer.classList.add(isSet ? 'platform-soundcloud-set' : 'platform-soundcloud');
    } else if (platform === 'spotify') {
      const isAlbum = release.embedUrl && release.embedUrl.includes('/album/');
      playerContainer.classList.add(isAlbum ? 'platform-spotify-album' : 'platform-spotify-track');
    }
    // YouTube / Discogs: no class, uses default 16:9 padding-top
  }

  // ── Persistent Port to Service Worker ──────────────────────

  function connectPort() {
    port = chrome.runtime.connect({ name: 'side-panel' });
    port.onMessage.addListener((msg) => {
      if (msg.type === 'queue-updated') {
        loadState();
      }
    });
    port.onDisconnect.addListener(() => {
      setTimeout(connectPort, 1000);
    });
  }

  // ── State Persistence ──────────────────────────────────────

  async function loadState() {
    const data = await chrome.storage.local.get([
      'playerQueue',
      'currentReleaseIndex',
      'currentVideoIndex',
    ]);
    queue = data.playerQueue || [];
    currentReleaseIndex = data.currentReleaseIndex || 0;
    currentVideoIndex = data.currentVideoIndex || 0;

    // Clamp indices
    if (currentReleaseIndex >= queue.length) currentReleaseIndex = 0;
    if (queue.length > 0 && queue[currentReleaseIndex]) {
      const release = queue[currentReleaseIndex];
      if (!hasEmbed(release)) {
        const vids = release.videos || [];
        if (currentVideoIndex >= vids.length) currentVideoIndex = 0;
      }
    }

    renderQueue();
    updateNowPlaying();
  }

  async function saveState() {
    await chrome.storage.local.set({
      playerQueue: queue,
      currentReleaseIndex,
      currentVideoIndex,
    });
  }

  // ── Unified Embed Loader ───────────────────────────────────

  function loadEmbed(release) {
    const platform = getReleasePlatform(release);
    setPlayerContainerClass(release);
    clearFallbackTimer();

    // Hide fallback from previous load
    embedFallback.style.display = 'none';

    if (release.embedUrl) {
      // Native embed — Bandcamp, SoundCloud, Spotify
      // SC widget fix: content script overrides document.referrer in MAIN world
      iframe.src = release.embedUrl;
    } else if (release.videos && release.videos[currentVideoIndex]) {
      // YouTube — declarativeNetRequest sets Referer header to bypass Error 153
      const vid = release.videos[currentVideoIndex].videoId;
      iframe.src = `https://www.youtube-nocookie.com/embed/${vid}?autoplay=1&rel=0`;
      embedFallbackLink.href = `https://www.youtube.com/watch?v=${vid}`;

      // Fallback auto-advance for YouTube: use duration if available
      const duration = release.videos[currentVideoIndex].duration || 0;
      if (duration > 0) {
        const timeoutMs = (duration + 10) * 1000;
        const cappedMs = Math.min(timeoutMs, 15 * 60 * 1000);
        fallbackTimer = setTimeout(() => advanceTrack(), cappedMs);
      }
    } else {
      return; // Nothing to play
    }

    iframe.style.display = 'block';
    placeholder.style.display = 'none';
    isPlaying = true;
  }

  function clearFallbackTimer() {
    if (fallbackTimer) {
      clearTimeout(fallbackTimer);
      fallbackTimer = null;
    }
  }

  // ── Track Title Parser ─────────────────────────────────────

  function parseTrackTitle(videoTitle, releaseArtist) {
    let cleaned = videoTitle
      .replace(/\s*\/\/.*$/, '')
      .replace(/\s*\(\d{4}\)\s*$/, '')
      .trim();
    const sep = cleaned.indexOf(' - ');
    if (sep > 0) return { artist: cleaned.substring(0, sep).trim(), title: cleaned.substring(sep + 3).trim() };
    return { artist: releaseArtist, title: cleaned };
  }

  // ── Playback Control ───────────────────────────────────────

  function playCurrentVideo() {
    if (queue.length === 0) return;
    const release = queue[currentReleaseIndex];
    if (!release) return;

    loadEmbed(release);
    updateNowPlaying();
    renderQueue();
    saveState();
  }

  function advanceTrack() {
    clearFallbackTimer();
    if (queue.length === 0) return;
    const release = queue[currentReleaseIndex];

    if (hasEmbed(release)) {
      // Embeds handle their own tracks — skip to next release
      if (currentReleaseIndex < queue.length - 1) {
        currentReleaseIndex++;
        currentVideoIndex = 0;
      } else {
        isPlaying = false;
        updateNowPlaying();
        saveState();
        return;
      }
    } else {
      // YouTube — advance through videos within release
      if (currentVideoIndex < release.videos.length - 1) {
        currentVideoIndex++;
      } else if (currentReleaseIndex < queue.length - 1) {
        currentReleaseIndex++;
        currentVideoIndex = 0;
      } else {
        isPlaying = false;
        updateNowPlaying();
        saveState();
        return;
      }
    }

    playCurrentVideo();
  }

  function prevTrack() {
    clearFallbackTimer();
    if (queue.length === 0) return;
    const release = queue[currentReleaseIndex];

    if (hasEmbed(release)) {
      if (currentReleaseIndex > 0) {
        currentReleaseIndex--;
        currentVideoIndex = 0;
      }
    } else {
      if (currentVideoIndex > 0) {
        currentVideoIndex--;
      } else if (currentReleaseIndex > 0) {
        currentReleaseIndex--;
        currentVideoIndex = queue[currentReleaseIndex].videos.length - 1;
      } else {
        currentVideoIndex = 0;
      }
    }

    playCurrentVideo();
  }

  function nextRelease() {
    clearFallbackTimer();
    if (queue.length === 0) return;

    if (currentReleaseIndex < queue.length - 1) {
      currentReleaseIndex++;
      currentVideoIndex = 0;
      playCurrentVideo();
    }
  }

  function playRelease(releaseIdx, videoIdx = 0) {
    clearFallbackTimer();
    if (releaseIdx < 0 || releaseIdx >= queue.length) return;
    currentReleaseIndex = releaseIdx;
    currentVideoIndex = videoIdx;
    playCurrentVideo();
  }

  // ── UI Updates ─────────────────────────────────────────────

  function updateNowPlaying() {
    if (queue.length === 0 || !queue[currentReleaseIndex]) {
      nowPlayingTitle.textContent = 'Nothing playing';
      nowPlayingRelease.textContent = '';
      return;
    }

    const release = queue[currentReleaseIndex];

    if (hasEmbed(release)) {
      nowPlayingTitle.textContent = release.title || 'Playing';
      nowPlayingRelease.textContent = release.artist || '';
    } else {
      const video = release.videos?.[currentVideoIndex];
      nowPlayingTitle.textContent = video ? video.title : 'No video';
      nowPlayingRelease.textContent = `${release.artist} \u2014 ${release.title}`;
    }
  }

  function renderQueue() {
    queueList.innerHTML = '';

    if (queue.length === 0) {
      queueEmpty.classList.add('visible');
      queueCount.textContent = '';
      return;
    }

    queueEmpty.classList.remove('visible');
    queueCount.textContent = `(${queue.length})`;

    queue.forEach((release, rIdx) => {
      const item = document.createElement('div');
      item.className = 'queue-item' + (rIdx === currentReleaseIndex ? ' active' : '');

      const header = document.createElement('div');
      header.className = 'queue-item-header';

      if (release.thumb) {
        const thumb = document.createElement('img');
        thumb.className = 'queue-item-thumb';
        thumb.src = release.thumb;
        thumb.alt = '';
        thumb.loading = 'lazy';
        header.appendChild(thumb);
      }

      const info = document.createElement('div');
      info.className = 'queue-item-info';

      const artist = document.createElement('div');
      artist.className = 'queue-item-artist';
      artist.textContent = release.artist || 'Unknown Artist';
      info.appendChild(artist);

      const title = document.createElement('div');
      title.className = 'queue-item-title';
      title.textContent = release.title || '';
      info.appendChild(title);

      const meta = document.createElement('div');
      meta.className = 'queue-item-meta';

      const platform = getReleasePlatform(release);
      const badge = document.createElement('span');
      badge.className = `queue-item-platform ${platform}`;
      badge.textContent = platform;
      meta.appendChild(badge);

      const videoCount = (!hasEmbed(release) && release.videos) ? release.videos.length : 0;
      const trackCount = (release.tracks && release.tracks.length > 0) ? release.tracks.length : videoCount;
      if (trackCount > 0) {
        const trackText = `${trackCount} track${trackCount !== 1 ? 's' : ''}`;
        meta.appendChild(document.createTextNode(trackText));
      }
      if (release.year) {
        meta.appendChild(document.createTextNode(` \u00B7 ${release.year}`));
      }
      info.appendChild(meta);

      header.appendChild(info);

      const actions = document.createElement('div');
      actions.className = 'queue-item-actions';

      const playBtn = document.createElement('button');
      playBtn.title = 'Play this release';
      playBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><polygon points="1,0 10,5 1,10"/></svg>';
      playBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        playRelease(rIdx);
      });
      actions.appendChild(playBtn);

      const digBtn = document.createElement('button');
      digBtn.className = 'dig-btn';
      digBtn.title = 'Add release to Wantlist';
      digBtn.textContent = 'Wantlist';
      digBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        addReleaseToWantedList(release, digBtn);
      });
      actions.appendChild(digBtn);

      const removeBtn = document.createElement('button');
      removeBtn.className = 'remove-btn';
      removeBtn.title = 'Remove from queue';
      removeBtn.textContent = '\u00D7';
      removeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        removeFromQueue(rIdx);
      });
      actions.appendChild(removeBtn);

      header.appendChild(actions);
      item.appendChild(header);

      // Track sub-list
      const videoList = (!hasEmbed(release) && release.videos && release.videos.length > 0) ? release.videos : null;
      const trackInfoList = (release.tracks && release.tracks.length > 0) ? release.tracks : null;

      if (videoList) {
        const tracks = document.createElement('div');
        tracks.className = 'queue-item-tracks';

        videoList.forEach((video, vIdx) => {
          const track = document.createElement('div');
          track.className = 'queue-track' +
            (rIdx === currentReleaseIndex && vIdx === currentVideoIndex ? ' playing' : '');

          const idx = document.createElement('span');
          idx.className = 'queue-track-index';
          idx.textContent = vIdx + 1;
          track.appendChild(idx);

          const tTitle = document.createElement('span');
          tTitle.className = 'queue-track-title';
          tTitle.textContent = video.title;
          track.appendChild(tTitle);

          const trackDigBtn = document.createElement('button');
          trackDigBtn.className = 'queue-track-dig';
          trackDigBtn.title = 'Add track to Wantlist';
          trackDigBtn.textContent = 'Wantlist';
          trackDigBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            addTrackToWantedList(release, video, trackDigBtn);
          });
          track.appendChild(trackDigBtn);

          track.addEventListener('click', (e) => {
            if (e.target.closest('.queue-track-dig')) return;
            e.stopPropagation();
            playRelease(rIdx, vIdx);
          });

          tracks.appendChild(track);
        });

        item.appendChild(tracks);
      } else if (trackInfoList) {
        const tracks = document.createElement('div');
        tracks.className = 'queue-item-tracks';

        trackInfoList.forEach((t, tIdx) => {
          const track = document.createElement('div');
          track.className = 'queue-track';

          const idx = document.createElement('span');
          idx.className = 'queue-track-index';
          idx.textContent = t.track_num || (tIdx + 1);
          track.appendChild(idx);

          const tTitle = document.createElement('span');
          tTitle.className = 'queue-track-title';
          tTitle.textContent = t.title;
          track.appendChild(tTitle);

          const trackDigBtn = document.createElement('button');
          trackDigBtn.className = 'queue-track-dig';
          trackDigBtn.title = 'Add track to Wantlist';
          trackDigBtn.textContent = 'Wantlist';
          trackDigBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            addEmbedTrackToWantedList(release, t.title, trackDigBtn);
          });
          track.appendChild(trackDigBtn);

          tracks.appendChild(track);
        });

        item.appendChild(tracks);
      }

      queueList.appendChild(item);
    });
  }

  // ── Queue Actions ──────────────────────────────────────────

  async function removeFromQueue(index) {
    if (index < 0 || index >= queue.length) return;

    const wasPlaying = index === currentReleaseIndex;
    queue.splice(index, 1);

    if (queue.length === 0) {
      currentReleaseIndex = 0;
      currentVideoIndex = 0;
      iframe.src = '';
      iframe.style.display = 'none';
      placeholder.style.display = 'flex';

      playerContainer.className = '';
      isPlaying = false;
      clearFallbackTimer();
    } else if (index < currentReleaseIndex) {
      currentReleaseIndex--;
    } else if (wasPlaying) {
      if (currentReleaseIndex >= queue.length) {
        currentReleaseIndex = queue.length - 1;
      }
      currentVideoIndex = 0;
      playCurrentVideo();
    }

    await saveState();
    chrome.runtime.sendMessage({ type: 'dig:removeFromQueue', data: { index } }).catch(() => {});
    renderQueue();
    updateNowPlaying();
  }

  async function clearQueue() {
    queue = [];
    currentReleaseIndex = 0;
    currentVideoIndex = 0;
    iframe.src = '';
    iframe.style.display = 'none';
    placeholder.style.display = 'flex';
    playerContainer.className = '';
    isPlaying = false;
    clearFallbackTimer();

    await saveState();
    chrome.runtime.sendMessage({ type: 'dig:clearQueue' }).catch(() => {});
    renderQueue();
    updateNowPlaying();
  }

  // ── Wantlist Actions (local storage) ───────────────────────

  async function addReleaseToWantedList(release, btn) {
    if (btn.classList.contains('added')) return;

    btn.textContent = '...';
    try {
      const platform = getReleasePlatform(release);
      const isAlbum = platform === 'discogs' || platform === 'bandcamp' ||
        (platform === 'spotify' && release.embedUrl && release.embedUrl.includes('/album/'));

      const data = {
        artist: release.artist || '',
        source_url: release.source_url || '',
        source_site: platform,
      };

      if (isAlbum) {
        data.title = '';
        data.release_name = release.title || '';
      } else {
        data.title = release.title || '';
        data.release_name = '';
      }

      const result = await chrome.runtime.sendMessage({ type: 'dig:add', data });

      if (result && (result.created || result.duplicate)) {
        btn.classList.add('added');
        btn.textContent = '\u2713';
        btn.title = result.duplicate ? 'Already in Wantlist' : 'Added to Wantlist';
      } else if (result && result.error) {
        btn.textContent = '!';
        btn.title = result.error;
      }
    } catch (err) {
      btn.textContent = '!';
      btn.title = err.message;
    }
  }

  async function addTrackToWantedList(release, video, btn) {
    if (btn.classList.contains('added')) return;

    btn.textContent = '...';
    try {
      const parsed = parseTrackTitle(video.title, release.artist || '');
      const result = await chrome.runtime.sendMessage({
        type: 'dig:add',
        data: {
          artist: parsed.artist,
          title: parsed.title,
          release_name: release.title || '',
          source_url: release.source_url || '',
          source_site: getReleasePlatform(release),
        },
      });

      if (result && (result.created || result.duplicate)) {
        btn.classList.add('added');
        btn.textContent = '\u2713';
        btn.title = result.duplicate ? 'Already in Wantlist' : 'Added to Wantlist';
      } else if (result && result.error) {
        btn.textContent = '!';
        btn.title = result.error;
      }
    } catch (err) {
      btn.textContent = '!';
      btn.title = err.message;
    }
  }

  async function addEmbedTrackToWantedList(release, trackTitle, btn) {
    if (btn.classList.contains('added')) return;

    btn.textContent = '...';
    try {
      const result = await chrome.runtime.sendMessage({
        type: 'dig:add',
        data: {
          artist: release.artist || '',
          title: trackTitle || '',
          release_name: release.title || '',
          source_url: release.source_url || '',
          source_site: getReleasePlatform(release),
        },
      });

      if (result && (result.created || result.duplicate)) {
        btn.classList.add('added');
        btn.textContent = '\u2713';
        btn.title = result.duplicate ? 'Already in Wantlist' : 'Added to Wantlist';
      } else if (result && result.error) {
        btn.textContent = '!';
        btn.title = result.error;
      }
    } catch (err) {
      btn.textContent = '!';
      btn.title = err.message;
    }
  }

  // ── YouTube Embed Error Detection ──────────────────────────

  // YouTube posts messages when embeds fail (Error 150/153)
  window.addEventListener('message', (event) => {
    if (event.origin !== 'https://www.youtube.com' && event.origin !== 'https://www.youtube-nocookie.com') return;
    try {
      const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
      // YouTube IFrame API error codes: 150 = blocked by owner, 101 = same, 2 = invalid ID
      if (data?.event === 'onError' || data?.info?.errorCode ||
          (data?.event === 'infoDelivery' && data?.info?.playerState === -1)) {
        showYouTubeFallback();
      }
    } catch (e) {
      // Not a JSON message from YouTube — ignore
    }
  });

  function showYouTubeFallback() {
    if (queue.length === 0) return;
    const release = queue[currentReleaseIndex];
    if (!release || hasEmbed(release)) return; // Only for YouTube embeds

    iframe.style.display = 'none';
    embedFallback.style.display = 'flex';
  }

  // ── Transport Event Listeners ──────────────────────────────

  btnNext.addEventListener('click', () => advanceTrack());
  btnPrev.addEventListener('click', () => prevTrack());
  btnNextRelease.addEventListener('click', () => nextRelease());
  btnClearQueue.addEventListener('click', () => clearQueue());

  // ── Init ───────────────────────────────────────────────────

  connectPort();
  loadState();
})();
