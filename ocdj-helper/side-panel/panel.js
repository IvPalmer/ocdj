// OCDJ Dig — Side Panel Player
// Multi-platform embeds: YouTube (via backend proxy), Bandcamp, Spotify (via embed proxy),
// SoundCloud (external player — embed blocked by verification)

(() => {
  'use strict';

  // ── SVG Icons ────────────────────────────────────────────────

  const ICON_PLAY = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><polygon points="4,1 14,8 4,15"/></svg>';
  const ICON_PAUSE = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><rect x="2" y="1" width="4" height="14"/><rect x="10" y="1" width="4" height="14"/></svg>';

  // ── State ──────────────────────────────────────────────────

  let queue = [];
  let currentReleaseIndex = 0;
  let currentVideoIndex = 0;
  let isPlaying = false;
  let currentPlatform = null; // 'youtube'|'discogs'|'bandcamp'|'soundcloud'|'spotify'
  let currentIsEmbed = false; // true when playing embed (not YouTube video-by-video)
  let port = null;
  let fallbackTimer = null;
  let gotPlaybackEvent = false;
  let backendUrl = 'http://localhost:8002';

  // ── DOM Refs ───────────────────────────────────────────────

  const iframe = document.getElementById('embed-player');
  const playerContainer = document.getElementById('player-container');
  const placeholder = document.getElementById('player-placeholder');
  const nowPlayingTitle = document.getElementById('now-playing-title');
  const nowPlayingRelease = document.getElementById('now-playing-release');
  const btnPlayPause = document.getElementById('btn-play-pause');
  const btnPrev = document.getElementById('btn-prev');
  const btnNext = document.getElementById('btn-next');
  const btnNextRelease = document.getElementById('btn-next-release');
  const btnClearQueue = document.getElementById('btn-clear-queue');
  const queueList = document.getElementById('queue-list');
  const queueEmpty = document.getElementById('queue-empty');
  const queueCount = document.getElementById('queue-count');
  const externalPlayer = document.getElementById('external-player');
  const externalPlayerArt = document.getElementById('external-player-art');
  const externalPlayerTitle = document.getElementById('external-player-title');
  const externalPlayerArtist = document.getElementById('external-player-artist');
  const externalPlayerLink = document.getElementById('external-player-link');

  // ── Platform Helpers ───────────────────────────────────────

  function hasEmbed(release) {
    return !!release.embedUrl;
  }

  function getReleasePlatform(release) {
    return release.platform || 'discogs';
  }

  function setPlayerContainerClass(release) {
    // Remove all platform classes
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
    // YouTube / Discogs: no class, uses default padding-top: 56.25%
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
      'backendUrl',
    ]);
    queue = data.playerQueue || [];
    currentReleaseIndex = data.currentReleaseIndex || 0;
    currentVideoIndex = data.currentVideoIndex || 0;
    backendUrl = data.backendUrl || 'http://localhost:8002';

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

  // ── YouTube via Backend Proxy ────────────────────────────────
  // The backend serves /api/dig/player/?v=VIDEO_ID — a minimal HTML page
  // that embeds YouTube with enablejsapi=1.

  function loadYouTubeVideo(videoId, duration) {
    setPlayerContainerClass({ platform: 'youtube' });
    const src = `${backendUrl}/api/dig/player/?v=${videoId}`;
    if (externalPlayer) externalPlayer.style.display = 'none';
    iframe.src = src;
    iframe.style.display = 'block';
    placeholder.style.display = 'none';
    isPlaying = true;
    currentPlatform = 'youtube';
    currentIsEmbed = false;
    btnPlayPause.innerHTML = ICON_PAUSE;

    // Fallback: if no playback event within duration+15s (or 8s minimum), auto-advance
    gotPlaybackEvent = false;
    clearFallbackTimer();
    const timeoutMs = Math.max(8000, ((duration || 300) + 15) * 1000);
    const cappedMs = Math.min(timeoutMs, 15 * 60 * 1000);
    fallbackTimer = setTimeout(() => {
      if (!gotPlaybackEvent) {
        console.log('[ocdj] Fallback timer expired, advancing');
        advanceTrack();
      }
    }, cappedMs);
  }

  // ── Native Platform Embeds ─────────────────────────────────

  function loadNativeEmbed(release) {
    const platform = getReleasePlatform(release);
    clearFallbackTimer();
    currentPlatform = platform;
    currentIsEmbed = true;

    setPlayerContainerClass(release);
    if (externalPlayer) externalPlayer.style.display = 'none';

    if (platform === 'bandcamp' && release.source_url) {
      // Custom Bandcamp player with HTML5 audio (autoplay + transport control)
      const params = new URLSearchParams({
        url: release.source_url,
        thumb: release.thumb || '',
        title: release.title || '',
        artist: release.artist || '',
      });
      iframe.src = `${backendUrl}/api/dig/bandcamp-player/?${params}`;
    } else {
      // Other embeds go through the proxy (wraps in localhost origin)
      const proxyUrl = `${backendUrl}/api/dig/embed/?url=${encodeURIComponent(release.embedUrl)}`;
      iframe.src = proxyUrl;
    }

    iframe.style.display = 'block';
    placeholder.style.display = 'none';
    isPlaying = true;
    btnPlayPause.innerHTML = ICON_PAUSE;
  }

  function loadExternalPlayer(release) {
    // External player card for platforms that block embeds
    playerContainer.className = 'platform-external';
    iframe.src = '';
    iframe.style.display = 'none';
    placeholder.style.display = 'none';

    if (externalPlayer) {
      externalPlayerArt.src = release.thumb || '';
      externalPlayerArt.style.display = release.thumb ? 'block' : 'none';
      externalPlayerTitle.textContent = release.title || '';
      externalPlayerArtist.textContent = release.artist || '';
      const platform = getReleasePlatform(release);
      const platformNames = { soundcloud: 'SoundCloud', bandcamp: 'Bandcamp' };
      externalPlayerLink.textContent = `Open in ${platformNames[platform] || platform}`;
      externalPlayerLink.href = release.source_url || release.embedUrl || '#';
      externalPlayer.style.display = 'flex';
    }
  }

  function clearFallbackTimer() {
    if (fallbackTimer) {
      clearTimeout(fallbackTimer);
      fallbackTimer = null;
    }
  }

  function postCommand(func) {
    if (!iframe.contentWindow) return;
    // Only works for YouTube embeds via backend proxy
    if (currentPlatform !== 'youtube' && currentPlatform !== 'discogs') return;
    iframe.contentWindow.postMessage({ action: 'command', func }, '*');
  }

  function playVideo() {
    if (currentIsEmbed) return; // Embeds control themselves
    postCommand('playVideo');
    isPlaying = true;
    btnPlayPause.innerHTML = ICON_PAUSE;
  }

  function pauseVideo() {
    if (currentIsEmbed) return; // Embeds control themselves
    postCommand('pauseVideo');
    isPlaying = false;
    btnPlayPause.innerHTML = ICON_PLAY;
  }

  // Listen for YouTube events relayed through the backend proxy page
  window.addEventListener('message', (e) => {
    // Accept messages from our backend proxy only
    if (!e.origin.includes('localhost') && !e.origin.includes('127.0.0.1')) return;

    let data;
    try {
      data = typeof e.data === 'string' ? JSON.parse(e.data) : e.data;
    } catch {
      return;
    }

    if (!data || !data.event) return;

    // YouTube error — skip to next
    if (data.event === 'onError') {
      console.log('[ocdj] YouTube error, skipping:', data.info);
      gotPlaybackEvent = true;
      clearFallbackTimer();
      advanceTrack();
      return;
    }

    // YouTube state: 0=ended, 1=playing, 2=paused, -1=unstarted
    if (data.event === 'onStateChange') {
      const state = data.info;
      if (state === 0) {
        gotPlaybackEvent = true;
        clearFallbackTimer();
        advanceTrack();
      } else if (state === 1) {
        gotPlaybackEvent = true;
        clearFallbackTimer();
        isPlaying = true;
        btnPlayPause.innerHTML = ICON_PAUSE;
      } else if (state === 2) {
        gotPlaybackEvent = true;
        isPlaying = false;
        btnPlayPause.innerHTML = ICON_PLAY;
      }
    }

    if (data.event === 'infoDelivery' && data.info) {
      if (data.info.playerState === 1) {
        gotPlaybackEvent = true;
        clearFallbackTimer();
      }
      if (data.info.playerState === 0) {
        gotPlaybackEvent = true;
        clearFallbackTimer();
        advanceTrack();
      }
    }

    // Custom Bandcamp player state events
    if (data.event === 'bandcamp-state') {
      if (data.state === 'playing') {
        isPlaying = true;
        btnPlayPause.innerHTML = ICON_PAUSE;
      } else if (data.state === 'paused') {
        isPlaying = false;
        btnPlayPause.innerHTML = ICON_PLAY;
      } else if (data.state === 'ended') {
        // All tracks in the Bandcamp release finished — advance to next release
        advanceTrack();
      }
    }
  });

  // ── Track Title Parser ───────────────────────────────────────

  function parseTrackTitle(videoTitle, releaseArtist) {
    let cleaned = videoTitle
      .replace(/\s*\/\/.*$/, '')           // "// Phone Traxxx (2019)"
      .replace(/\s*\(\d{4}\)\s*$/, '')     // trailing (Year)
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

    if (hasEmbed(release)) {
      // Native embed: Bandcamp, Spotify, YouTube playlists, SoundCloud (external)
      loadNativeEmbed(release);
    } else if (release.videos && release.videos.length > 0) {
      // YouTube / Discogs — YouTube video via backend proxy
      const video = release.videos[currentVideoIndex];
      if (!video) return;
      loadYouTubeVideo(video.videoId, video.duration);
    } else {
      return;
    }

    updateNowPlaying();
    renderQueue();
    saveState();
  }

  function advanceTrack() {
    clearFallbackTimer();
    if (queue.length === 0) return;
    const release = queue[currentReleaseIndex];

    if (hasEmbed(release)) {
      // Embeds — skip to next release (embed handles its own tracks)
      if (currentReleaseIndex < queue.length - 1) {
        currentReleaseIndex++;
        currentVideoIndex = 0;
      } else {
        isPlaying = false;
        btnPlayPause.innerHTML = ICON_PLAY;
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
        btnPlayPause.innerHTML = ICON_PLAY;
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
      // Embeds — go to previous release
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

      // Platform badge
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
      digBtn.title = 'Add release to Wanted List';
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

      // Track sub-list — YouTube/Discogs videos (playable) or Bandcamp tracks (display only)
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

          // Per-track "Add to Wanted" button
          const trackDigBtn = document.createElement('button');
          trackDigBtn.className = 'queue-track-dig';
          trackDigBtn.title = 'Add track to Wanted List';
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
        // Display-only track list (Bandcamp albums etc.)
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

          // Per-track "Add to Wanted" button
          const trackDigBtn = document.createElement('button');
          trackDigBtn.className = 'queue-track-dig';
          trackDigBtn.title = 'Add track to Wanted List';
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
      if (externalPlayer) externalPlayer.style.display = 'none';
      placeholder.style.display = 'flex';
      playerContainer.className = '';
      isPlaying = false;
      currentPlatform = null;
      currentIsEmbed = false;
      clearFallbackTimer();
      btnPlayPause.innerHTML = ICON_PLAY;
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
    chrome.runtime.sendMessage({ type: 'dig:removeFromQueue', data: { index } });
    renderQueue();
    updateNowPlaying();
  }

  async function clearQueue() {
    queue = [];
    currentReleaseIndex = 0;
    currentVideoIndex = 0;
    iframe.src = '';
    iframe.style.display = 'none';
    if (externalPlayer) externalPlayer.style.display = 'none';
    placeholder.style.display = 'flex';
    playerContainer.className = '';
    isPlaying = false;
    currentPlatform = null;
    currentIsEmbed = false;
    clearFallbackTimer();
    btnPlayPause.innerHTML = ICON_PLAY;

    await saveState();
    chrome.runtime.sendMessage({ type: 'dig:clearQueue' });
    renderQueue();
    updateNowPlaying();
  }

  async function addReleaseToWantedList(release, btn) {
    if (btn.classList.contains('added')) return;

    btn.textContent = '...';
    try {
      const platform = getReleasePlatform(release);

      // Album-level platforms: title is empty, release_name is the album name
      // Track-level platforms: title IS the track title, no release_name
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
        btn.title = result.duplicate ? 'Already in Wanted List' : 'Added to Wanted List';
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
        btn.title = result.duplicate ? 'Already in Wanted List' : 'Added to Wanted List';
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
        btn.title = result.duplicate ? 'Already in Wanted List' : 'Added to Wanted List';
      } else if (result && result.error) {
        btn.textContent = '!';
        btn.title = result.error;
      }
    } catch (err) {
      btn.textContent = '!';
      btn.title = err.message;
    }
  }

  // ── Transport Event Listeners ──────────────────────────────

  btnPlayPause.addEventListener('click', () => {
    if (queue.length === 0) return;
    if (!iframe.src || iframe.style.display === 'none') {
      playCurrentVideo();
      return;
    }
    if (currentIsEmbed) {
      // Send play/pause to embed via proxy relay
      const action = isPlaying ? 'pause' : 'play';
      iframe.contentWindow.postMessage({ action }, '*');
      isPlaying = !isPlaying;
      btnPlayPause.innerHTML = isPlaying ? ICON_PAUSE : ICON_PLAY;
      return;
    }
    if (isPlaying) {
      pauseVideo();
    } else {
      playVideo();
    }
  });

  btnNext.addEventListener('click', () => {
    // For Bandcamp custom player, forward next to the player (within-release tracks)
    if (currentIsEmbed && currentPlatform === 'bandcamp' && iframe.contentWindow) {
      iframe.contentWindow.postMessage({ action: 'next' }, '*');
    } else {
      advanceTrack();
    }
  });
  btnPrev.addEventListener('click', () => {
    // For Bandcamp custom player, forward prev to the player
    if (currentIsEmbed && currentPlatform === 'bandcamp' && iframe.contentWindow) {
      iframe.contentWindow.postMessage({ action: 'prev' }, '*');
    } else {
      prevTrack();
    }
  });
  btnNextRelease.addEventListener('click', () => nextRelease());
  btnClearQueue.addEventListener('click', () => clearQueue());

  // ── Init ───────────────────────────────────────────────────

  connectPort();
  loadState();
})();
