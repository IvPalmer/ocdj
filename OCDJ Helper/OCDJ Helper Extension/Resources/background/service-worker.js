// OCDJ Standalone — Background Service Worker (Safari)
// Syncs wantlist to OCDJ backend, falls back to local storage if offline
// Safari-compatible: no sidePanel API, guarded world:'MAIN' registration

const DEFAULT_BACKEND = 'http://localhost:8002';
let backendUrl = DEFAULT_BACKEND;

// Load backend URL on startup and install
function loadBackendUrl() {
  chrome.storage.local.get(['backendUrl'], (result) => {
    backendUrl = result.backendUrl || DEFAULT_BACKEND;
  });
}
chrome.runtime.onStartup?.addListener(loadBackendUrl);
chrome.runtime.onInstalled?.addListener(loadBackendUrl);
loadBackendUrl();

chrome.storage.onChanged.addListener((changes) => {
  if (changes.backendUrl) {
    backendUrl = changes.backendUrl.newValue || DEFAULT_BACKEND;
  }
});

async function apiCall(endpoint, method = 'GET', body = null) {
  const opts = {
    method,
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(10000),
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${backendUrl}${endpoint}`, opts);
  let data;
  try {
    data = await resp.json();
  } catch (e) {
    throw new Error(`Server error (${resp.status})`);
  }
  if (!resp.ok) throw new Error(data.detail || data.error || JSON.stringify(data));
  return data;
}

// ── Referer Header Rules ─────────────────────────────────
// YouTube and SoundCloud reject embeds from extension origins.
// Use declarativeNetRequest to set a proper Referer on embed requests.

chrome.runtime.onInstalled.addListener(async () => {
  // Register content script to override document.referrer in SC widget
  // Safari doesn't support world:'MAIN' — guard with try/catch
  try {
    await chrome.scripting.unregisterContentScripts({ ids: ['sc-widget-fix'] });
  } catch (e) {}
  try {
    await chrome.scripting.registerContentScripts([{
      id: 'sc-widget-fix',
      matches: ['*://w.soundcloud.com/*'],
      js: ['content/sc-widget-fix.js'],
      runAt: 'document_start',
      allFrames: true,
      world: 'MAIN',
    }]);
  } catch (e) {
    // Safari may not support world:'MAIN' — SC widget embeds may have referrer issues
    console.warn('Could not register MAIN world script (expected on Safari):', e.message);
  }

  await chrome.declarativeNetRequest.updateDynamicRules({
    removeRuleIds: [1, 2, 3],
    addRules: [
      {
        id: 1,
        priority: 1,
        action: {
          type: 'modifyHeaders',
          requestHeaders: [
            { header: 'Referer', operation: 'set', value: 'https://www.google.com/' },
          ],
        },
        condition: {
          urlFilter: '||www.youtube-nocookie.com/embed/',
          resourceTypes: ['sub_frame'],
        },
      },
      {
        id: 2,
        priority: 1,
        action: {
          type: 'modifyHeaders',
          requestHeaders: [
            { header: 'Referer', operation: 'set', value: 'https://soundcloud.com/' },
          ],
        },
        condition: {
          urlFilter: '||w.soundcloud.com/',
          resourceTypes: ['sub_frame'],
        },
      },
      {
        id: 3,
        priority: 1,
        action: {
          type: 'modifyHeaders',
          requestHeaders: [
            { header: 'Referer', operation: 'set', value: 'https://soundcloud.com/' },
          ],
        },
        condition: {
          urlFilter: '||api-v2.soundcloud.com/',
          resourceTypes: ['xmlhttprequest'],
        },
      },
    ],
  });
});

// ── Message Routing ──────────────────────────────────────

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

// ── Wantlist Helpers ─────────────────────────────────────

function normalizeStr(s) {
  return (s || '').toLowerCase().trim();
}

function isDuplicate(existing, item) {
  // Exact match on artist + title, or artist + release_name
  const a1 = normalizeStr(existing.artist);
  const a2 = normalizeStr(item.artist);
  const t1 = normalizeStr(existing.title);
  const t2 = normalizeStr(item.title);
  const r1 = normalizeStr(existing.release_name);
  const r2 = normalizeStr(item.release_name);

  if (a1 && a2 && a1 === a2) {
    if (t1 && t2 && t1 === t2) return true;
    if (r1 && r2 && r1 === r2) return true;
  }
  // Also match by source_url if both present
  if (existing.source_url && item.source_url &&
      normalizeStr(existing.source_url) === normalizeStr(item.source_url)) {
    return true;
  }
  return false;
}

// ── Discogs API ──────────────────────────────────────────

async function fetchDiscogsVideos(releaseId) {
  const { discogsToken } = await chrome.storage.local.get('discogsToken');
  const headers = { 'User-Agent': 'OCDJStandalone/1.0' };
  if (discogsToken) {
    headers['Authorization'] = `Discogs token=${discogsToken}`;
  }

  const resp = await fetch(`https://api.discogs.com/releases/${releaseId}`, {
    headers,
    signal: AbortSignal.timeout(10000),
  });

  if (!resp.ok) {
    throw new Error(`Discogs API error: ${resp.status}`);
  }

  const data = await resp.json();

  // Extract YouTube video IDs from data.videos[]
  const videos = (data.videos || [])
    .filter(v => v.uri && v.uri.includes('youtube.com'))
    .map(v => {
      const url = new URL(v.uri);
      const videoId = url.searchParams.get('v');
      if (!videoId) return null;
      return {
        videoId,
        title: v.title || '',
        duration: v.duration || 0,
      };
    })
    .filter(Boolean);

  return {
    artist: (data.artists || []).map(a => a.name).join(', ').replace(/\s*\(\d+\)/g, ''),
    title: data.title || '',
    year: data.year ? String(data.year) : '',
    thumb: data.images?.[0]?.uri150 || data.thumb || '',
    videos,
  };
}

// ── SoundCloud oEmbed ────────────────────────────────────

async function resolveSoundCloudEmbed(trackUrl) {
  const cleanUrl = trackUrl.split('?')[0];
  const resp = await fetch(
    `https://soundcloud.com/oembed?url=${encodeURIComponent(cleanUrl)}&format=json`,
    { signal: AbortSignal.timeout(5000) }
  );
  if (!resp.ok) return null;
  const data = await resp.json();
  const srcMatch = data.html?.match(/src="([^"]+)"/);
  if (!srcMatch) return null;
  return srcMatch[1].replace(/&amp;/g, '&');
}

// ── Message Handlers ─────────────────────────────────────

const messageHandlers = {
  'dig:add': async (data) => {
    // Try backend first, fall back to local-only
    try {
      const result = await apiCall('/api/dig/add/', 'POST', data);
      // Also store locally for popup display
      const { wantlist = [] } = await chrome.storage.local.get('wantlist');
      if (result.created) {
        wantlist.push({ ...data, added_at: Date.now() });
        await chrome.storage.local.set({ wantlist });
      }
      return result;
    } catch (e) {
      // Offline fallback: store locally
      const { wantlist = [] } = await chrome.storage.local.get('wantlist');
      const item = {
        artist: data.artist || '', title: data.title || '',
        release_name: data.release_name || '', catalog_number: data.catalog_number || '',
        label: data.label || '', source_url: data.source_url || '',
        source_site: data.source_site || '', notes: data.notes || '',
        added_at: Date.now(),
      };
      if (wantlist.some(existing => isDuplicate(existing, item))) {
        return { created: false, duplicate: true, fuzzy_score: 100 };
      }
      wantlist.push(item);
      await chrome.storage.local.set({ wantlist });
      return { created: true, item, offline: true };
    }
  },

  'dig:batch': async (data) => {
    try {
      const result = await apiCall('/api/dig/batch/', 'POST', data);
      // Also store locally
      const { wantlist = [] } = await chrome.storage.local.get('wantlist');
      for (const itemData of (data.items || [])) {
        const item = {
          artist: itemData.artist || '', title: itemData.title || '',
          release_name: itemData.release_name || '', catalog_number: itemData.catalog_number || '',
          label: itemData.label || '', source_url: itemData.source_url || data.source_url || '',
          source_site: itemData.source_site || data.source_site || '',
          notes: itemData.notes || '', added_at: Date.now(),
        };
        if (!wantlist.some(existing => isDuplicate(existing, item))) {
          wantlist.push(item);
        }
      }
      await chrome.storage.local.set({ wantlist });
      return result;
    } catch (e) {
      // Offline fallback
      const { wantlist = [] } = await chrome.storage.local.get('wantlist');
      const items = data.items || [];
      let created = 0;
      for (const itemData of items) {
        const item = {
          artist: itemData.artist || '', title: itemData.title || '',
          release_name: itemData.release_name || '', catalog_number: itemData.catalog_number || '',
          label: itemData.label || '', source_url: itemData.source_url || data.source_url || '',
          source_site: itemData.source_site || data.source_site || '',
          notes: itemData.notes || '', added_at: Date.now(),
        };
        if (!wantlist.some(existing => isDuplicate(existing, item))) {
          wantlist.push(item);
          created++;
        }
      }
      await chrome.storage.local.set({ wantlist });
      return { created, total: items.length, items, offline: true };
    }
  },

  'dig:check': async (data) => {
    try {
      return await apiCall('/api/dig/check/', 'POST', data);
    } catch (e) {
      // Offline fallback
      const { wantlist = [] } = await chrome.storage.local.get('wantlist');
      const items = data.items || [data];
      const results = items.map(item => ({
        exists: wantlist.some(existing => isDuplicate(existing, item)),
      }));
      return { results };
    }
  },

  'dig:queue': async (data, sender) => {
    const { releaseId, artist, title, source_url, thumb } = data;
    const releaseData = await fetchDiscogsVideos(releaseId);

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

    const { playerQueue = [] } = await chrome.storage.local.get('playerQueue');

    if (playerQueue.some(item => item.releaseId === releaseId)) {
      return { queued: true, duplicate: true };
    }

    playerQueue.push(queueItem);
    await chrome.storage.local.set({ playerQueue });

    return { queued: true, item: queueItem };
  },

  'dig:queue-yt': async (data, sender) => {
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

    return { queued: true, item: queueItem };
  },

  'dig:queue-track': async (data, sender) => {
    const { releaseId, trackTitle, artist, source_url } = data;
    if (!releaseId || !trackTitle) return { error: 'Missing releaseId or trackTitle' };

    const releaseData = await fetchDiscogsVideos(releaseId);
    if (!releaseData.videos || releaseData.videos.length === 0) {
      return { error: 'No YouTube videos found for this release' };
    }

    const needle = trackTitle.toLowerCase();
    let matched = releaseData.videos.find(v =>
      v.title.toLowerCase().includes(needle)
    );

    if (!matched) {
      const words = needle.split(/\s+/).filter(w => w.length > 2);
      matched = releaseData.videos.find(v => {
        const vt = v.title.toLowerCase();
        return words.filter(w => vt.includes(w)).length >= Math.ceil(words.length / 2);
      });
    }

    if (!matched) {
      if (releaseData.videos.length === 1) {
        matched = releaseData.videos[0];
      } else {
        return { error: `Could not match "${trackTitle}" to any video` };
      }
    }

    const queueItem = {
      releaseId,
      artist: artist || releaseData.artist || '',
      title: trackTitle,
      thumb: releaseData.thumb || '',
      year: releaseData.year || '',
      source_url: source_url || `https://www.discogs.com/release/${releaseId}`,
      videos: [matched],
    };

    const { playerQueue = [] } = await chrome.storage.local.get('playerQueue');
    if (playerQueue.some(item =>
      item.releaseId === releaseId && item.videos?.some(v => v.videoId === matched.videoId)
    )) {
      return { queued: true, duplicate: true };
    }

    playerQueue.push(queueItem);
    await chrome.storage.local.set({ playerQueue });

    return { queued: true, item: queueItem };
  },

  'dig:queue-embed': async (data, sender) => {
    let { platform, artist, title, embedUrl, thumb, source_url, tracks } = data;
    if (!embedUrl) return { error: 'No embed URL' };

    if (platform === 'soundcloud' && source_url) {
      try {
        const resolved = await resolveSoundCloudEmbed(source_url);
        if (resolved) embedUrl = resolved;
      } catch (e) {}
    }

    const queueItem = {
      releaseId: `${platform}-${Date.now()}`,
      platform,
      artist: artist || '',
      title: title || '',
      thumb: thumb || '',
      year: '',
      source_url: source_url || '',
      embedUrl,
      tracks: tracks || [],
      videos: [],
    };

    const { playerQueue = [] } = await chrome.storage.local.get('playerQueue');
    if (playerQueue.some(item =>
      (item.embedUrl && item.embedUrl === embedUrl) ||
      (item.source_url && source_url && item.source_url === source_url)
    )) {
      return { queued: true, duplicate: true };
    }

    playerQueue.push(queueItem);
    await chrome.storage.local.set({ playerQueue });

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
};
