// OCDJ Dig — Background Service Worker
// Handles API calls, connection state, badge updates, recent activity

const DEFAULT_BACKEND = 'http://localhost:8002';
const PING_INTERVAL = 60000; // 60s
const MAX_RECENT = 20;

let backendUrl = DEFAULT_BACKEND;
let connected = false;
let pingTimer = null;
let sidePanelPort = null;

// ── Init ─────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(['backendUrl'], (result) => {
    backendUrl = result.backendUrl || DEFAULT_BACKEND;
    pingBackend();
    startPingTimer();
  });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.storage.local.get(['backendUrl'], (result) => {
    backendUrl = result.backendUrl || DEFAULT_BACKEND;
    pingBackend();
    startPingTimer();
  });
});

// ── Ping / Connection State ──────────────────────────────────

async function pingBackend() {
  try {
    const resp = await fetch(`${backendUrl}/api/dig/status/`, {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
      signal: AbortSignal.timeout(5000),
    });
    if (resp.ok) {
      const data = await resp.json();
      connected = true;
      updateBadge(true);
      return data;
    }
    throw new Error(`HTTP ${resp.status}`);
  } catch (err) {
    connected = false;
    updateBadge(false);
    return null;
  }
}

function updateBadge(isConnected) {
  chrome.action.setBadgeBackgroundColor({
    color: isConnected ? '#22c55e' : '#ef4444',
  });
  chrome.action.setBadgeText({ text: isConnected ? '' : '!' });
}

function startPingTimer() {
  if (pingTimer) clearInterval(pingTimer);
  pingTimer = setInterval(pingBackend, PING_INTERVAL);
}

// ── API Helpers ──────────────────────────────────────────────

async function apiCall(endpoint, method = 'GET', body = null) {
  const opts = {
    method,
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
    },
    signal: AbortSignal.timeout(10000),
  };
  if (body) opts.body = JSON.stringify(body);

  const resp = await fetch(`${backendUrl}${endpoint}`, opts);

  let data;
  try {
    data = await resp.json();
  } catch (e) {
    const text = await resp.clone().text().catch(() => '');
    throw new Error(`Server error (${resp.status}): ${text.slice(0, 100)}`);
  }

  if (!resp.ok) {
    throw new Error(data.detail || data.error || JSON.stringify(data));
  }
  return data;
}

async function addToRecent(item) {
  const entry = {
    artist: item.artist || '',
    title: item.title || '',
    source_site: item.source_site || '',
    timestamp: Date.now(),
  };

  const { recentActivity = [] } = await chrome.storage.local.get('recentActivity');
  recentActivity.unshift(entry);
  if (recentActivity.length > MAX_RECENT) recentActivity.length = MAX_RECENT;
  await chrome.storage.local.set({ recentActivity });
}

// ── Message Routing ──────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return false;

  const handler = messageHandlers[msg.type];
  if (!handler) {
    sendResponse({ error: `Unknown message type: ${msg.type}` });
    return false;
  }

  handler(msg.data, sender)
    .then(sendResponse)
    .catch((err) => sendResponse({ error: err.message }));

  return true; // async response
});

const messageHandlers = {
  'dig:add': async (data) => {
    const result = await apiCall('/api/dig/add/', 'POST', data);
    if (result.created) {
      await addToRecent(data);
    }
    return result;
  },

  'dig:batch': async (data) => {
    const result = await apiCall('/api/dig/batch/', 'POST', data);
    if (result.created > 0) {
      for (const item of (result.items || []).slice(0, 5)) {
        await addToRecent({
          artist: item.artist,
          title: item.title,
          source_site: data.source_site,
        });
      }
    }
    return result;
  },

  'dig:check': async (data) => {
    return await apiCall('/api/dig/check/', 'POST', data);
  },

  'dig:import': async (data) => {
    return await apiCall('/api/wanted/import/trigger/', 'POST', data);
  },

  'dig:recognize': async (data) => {
    return await apiCall('/api/recognize/jobs/create/', 'POST', data);
  },

  'dig:queue': async (data, sender) => {
    const { releaseId, artist, title, source_url, thumb } = data;

    // Fetch videos from backend
    const releaseData = await apiCall(`/api/dig/videos/${releaseId}/`);

    if (!releaseData.videos || releaseData.videos.length === 0) {
      return { error: 'No YouTube videos found for this release' };
    }

    const queueItem = {
      releaseId,
      artist: releaseData.artist || artist || '',
      title: releaseData.title || title || '',
      thumb: releaseData.thumb || thumb || '',
      year: releaseData.year || '',
      source_url: source_url || `https://www.discogs.com/release/${releaseId}`,
      videos: releaseData.videos,
    };

    // Add to queue in storage
    const { playerQueue = [] } = await chrome.storage.local.get('playerQueue');

    // Skip if already in queue
    if (playerQueue.some(item => item.releaseId === releaseId)) {
      return { queued: true, duplicate: true };
    }

    playerQueue.push(queueItem);
    await chrome.storage.local.set({ playerQueue });

    // Notify side panel
    notifySidePanel();

    // Open side panel
    if (sender && sender.tab) {
      try {
        await chrome.sidePanel.open({ tabId: sender.tab.id });
      } catch (e) {
        // Side panel may already be open
      }
    }

    return { queued: true, item: queueItem };
  },

  'dig:queue-yt': async (data, sender) => {
    // Direct YouTube video queue (from YouTube content script)
    const { videoId, title, artist, source_url } = data;

    if (!videoId) return { error: 'No video ID' };

    const queueItem = {
      releaseId: `yt-${videoId}`,
      platform: 'youtube',
      artist: artist || '',
      title: title || '',
      thumb: `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`,
      year: '',
      source_url: source_url || `https://www.youtube.com/watch?v=${videoId}`,
      videos: [{ videoId, title: title || '', duration: 0 }],
    };

    const { playerQueue = [] } = await chrome.storage.local.get('playerQueue');
    if (playerQueue.some(item => item.videos?.some(v => v.videoId === videoId))) {
      return { queued: true, duplicate: true };
    }

    playerQueue.push(queueItem);
    await chrome.storage.local.set({ playerQueue });
    notifySidePanel();

    if (sender?.tab) {
      try { await chrome.sidePanel.open({ tabId: sender.tab.id }); } catch (e) {}
    }

    return { queued: true, item: queueItem };
  },

  'dig:queue-embed': async (data, sender) => {
    // Queue a native platform embed (Bandcamp, SoundCloud, Spotify, YouTube playlists)
    const { platform, artist, title, embedUrl, thumb, source_url, tracks } = data;

    if (!embedUrl) return { error: 'No embed URL' };

    const queueItem = {
      releaseId: `${platform}-${Date.now()}`,
      platform,
      artist: artist || '',
      title: title || '',
      thumb: thumb || '',
      year: '',
      source_url: source_url || '',
      embedUrl,
      tracks: tracks || [], // Track list for display (Bandcamp albums)
      videos: [], // Native embeds don't use videos array
    };

    const { playerQueue = [] } = await chrome.storage.local.get('playerQueue');
    // Dedup by source URL or embed URL
    if (playerQueue.some(item =>
      (item.embedUrl && item.embedUrl === embedUrl) ||
      (item.source_url && source_url && item.source_url === source_url)
    )) {
      return { queued: true, duplicate: true };
    }

    playerQueue.push(queueItem);
    await chrome.storage.local.set({ playerQueue });
    notifySidePanel();

    if (sender?.tab) {
      try { await chrome.sidePanel.open({ tabId: sender.tab.id }); } catch (e) {}
    }

    return { queued: true, item: queueItem };
  },

  'dig:queue-search': async (data, sender) => {
    // Search YouTube for a track and queue the first result
    const { artist, title, thumb, source_url, platform } = data;
    const query = `${artist || ''} ${title || ''}`.trim();
    if (!query) return { error: 'No search query' };

    const result = await apiCall(`/api/dig/yt-search/?q=${encodeURIComponent(query)}`);
    if (!result.videoId) return { error: 'No results found' };

    const queueItem = {
      releaseId: `yt-${result.videoId}`,
      platform: platform || 'discogs',
      artist: artist || '',
      title: title || result.title || '',
      thumb: thumb || `https://i.ytimg.com/vi/${result.videoId}/hqdefault.jpg`,
      year: '',
      source_url: source_url || `https://www.youtube.com/watch?v=${result.videoId}`,
      videos: [{ videoId: result.videoId, title: result.title || title || '', duration: result.duration || 0 }],
    };

    const { playerQueue = [] } = await chrome.storage.local.get('playerQueue');
    if (playerQueue.some(item => item.videos?.some(v => v.videoId === result.videoId))) {
      return { queued: true, duplicate: true };
    }

    playerQueue.push(queueItem);
    await chrome.storage.local.set({ playerQueue });
    notifySidePanel();

    if (sender?.tab) {
      try { await chrome.sidePanel.open({ tabId: sender.tab.id }); } catch (e) {}
    }

    return { queued: true, item: queueItem };
  },

  'dig:removeFromQueue': async (data) => {
    const { index } = data;
    const stored = await chrome.storage.local.get(['playerQueue', 'currentReleaseIndex', 'currentVideoIndex']);
    const queue = stored.playerQueue || [];
    if (index >= 0 && index < queue.length) {
      queue.splice(index, 1);
      let releaseIdx = stored.currentReleaseIndex || 0;
      if (index < releaseIdx) releaseIdx--;
      if (releaseIdx >= queue.length) releaseIdx = Math.max(0, queue.length - 1);
      await chrome.storage.local.set({
        playerQueue: queue,
        currentReleaseIndex: releaseIdx,
      });
    }
    return { ok: true };
  },

  'dig:clearQueue': async () => {
    await chrome.storage.local.set({
      playerQueue: [],
      currentReleaseIndex: 0,
      currentVideoIndex: 0,
    });
    return { ok: true };
  },

  'dig:status': async () => {
    return await pingBackend();
  },

  'dig:getConfig': async () => {
    return {
      backendUrl,
      connected,
    };
  },
};

// ── Side Panel Port ──────────────────────────────────────

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'side-panel') {
    sidePanelPort = port;
    port.onDisconnect.addListener(() => {
      if (sidePanelPort === port) sidePanelPort = null;
    });
  }
});

function notifySidePanel() {
  if (sidePanelPort) {
    try {
      sidePanelPort.postMessage({ type: 'queue-updated' });
    } catch (e) {
      sidePanelPort = null;
    }
  }
}

// ── Storage Change Listener ──────────────────────────────────

chrome.storage.onChanged.addListener((changes) => {
  if (changes.backendUrl) {
    backendUrl = changes.backendUrl.newValue || DEFAULT_BACKEND;
    pingBackend();
  }
});
