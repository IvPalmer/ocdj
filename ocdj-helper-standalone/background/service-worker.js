// OCDJ Standalone — Background Service Worker
// All data stored locally in chrome.storage.local — no backend needed

let sidePanelPort = null;

// ── Referer Header Rules ─────────────────────────────────
// YouTube and SoundCloud reject embeds from chrome-extension:// origins.
// Use declarativeNetRequest to set a proper Referer on embed requests.

chrome.runtime.onInstalled.addListener(async () => {
  // Register content script to override document.referrer in SC widget
  // (manifest content_scripts may not inject into extension page iframes)
  try {
    await chrome.scripting.unregisterContentScripts({ ids: ['sc-widget-fix'] });
  } catch (e) {}
  await chrome.scripting.registerContentScripts([{
    id: 'sc-widget-fix',
    matches: ['*://w.soundcloud.com/*'],
    js: ['content/sc-widget-fix.js'],
    runAt: 'document_start',
    allFrames: true,
    world: 'MAIN',
  }]);

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
  // Resolve the proper embed URL via SoundCloud's oEmbed API
  // The oEmbed response contains an iframe src with api.soundcloud.com/tracks/ID
  // which the SC widget accepts (unlike bare soundcloud.com URLs from extension context)
  const cleanUrl = trackUrl.split('?')[0];
  const resp = await fetch(
    `https://soundcloud.com/oembed?url=${encodeURIComponent(cleanUrl)}&format=json`,
    { signal: AbortSignal.timeout(5000) }
  );
  if (!resp.ok) return null;
  const data = await resp.json();
  const srcMatch = data.html?.match(/src="([^"]+)"/);
  if (!srcMatch) return null;
  // Decode HTML entities and return the embed URL
  return srcMatch[1].replace(/&amp;/g, '&');
}

// ── Message Handlers ─────────────────────────────────────

const messageHandlers = {
  'dig:add': async (data) => {
    const { wantlist = [] } = await chrome.storage.local.get('wantlist');

    const item = {
      artist: data.artist || '',
      title: data.title || '',
      release_name: data.release_name || '',
      catalog_number: data.catalog_number || '',
      label: data.label || '',
      source_url: data.source_url || '',
      source_site: data.source_site || '',
      notes: data.notes || '',
      added_at: Date.now(),
    };

    // Exact dedup
    if (wantlist.some(existing => isDuplicate(existing, item))) {
      return { created: false, duplicate: true, fuzzy_score: 100 };
    }

    wantlist.push(item);
    await chrome.storage.local.set({ wantlist });

    return { created: true, item };
  },

  'dig:batch': async (data) => {
    const { wantlist = [] } = await chrome.storage.local.get('wantlist');
    const items = data.items || [];
    let created = 0;

    for (const itemData of items) {
      const item = {
        artist: itemData.artist || '',
        title: itemData.title || '',
        release_name: itemData.release_name || '',
        catalog_number: itemData.catalog_number || '',
        label: itemData.label || '',
        source_url: itemData.source_url || data.source_url || '',
        source_site: itemData.source_site || data.source_site || '',
        notes: itemData.notes || '',
        added_at: Date.now(),
      };

      if (!wantlist.some(existing => isDuplicate(existing, item))) {
        wantlist.push(item);
        created++;
      }
    }

    await chrome.storage.local.set({ wantlist });
    return { created, total: items.length, items };
  },

  'dig:check': async (data) => {
    const { wantlist = [] } = await chrome.storage.local.get('wantlist');
    const items = data.items || [data];
    const results = items.map(item => ({
      exists: wantlist.some(existing => isDuplicate(existing, item)),
    }));
    return { results };
  },

  'dig:queue': async (data, sender) => {
    const { releaseId, artist, title, source_url, thumb } = data;

    // Fetch videos from Discogs API directly
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
    notifySidePanel();

    if (sender?.tab) {
      try { await chrome.sidePanel.open({ tabId: sender.tab.id }); } catch (e) {}
    }

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
    notifySidePanel();

    if (sender?.tab) {
      try { await chrome.sidePanel.open({ tabId: sender.tab.id }); } catch (e) {}
    }

    return { queued: true, item: queueItem };
  },

  'dig:queue-track': async (data, sender) => {
    const { releaseId, trackTitle, artist, source_url } = data;
    if (!releaseId || !trackTitle) return { error: 'Missing releaseId or trackTitle' };

    // Fetch videos from Discogs API for this release
    const releaseData = await fetchDiscogsVideos(releaseId);
    if (!releaseData.videos || releaseData.videos.length === 0) {
      return { error: 'No YouTube videos found for this release' };
    }

    // Match the track by title — Discogs video titles often contain the track name
    const needle = trackTitle.toLowerCase();
    let matched = releaseData.videos.find(v =>
      v.title.toLowerCase().includes(needle)
    );

    // Fallback: check if track title words appear in video title
    if (!matched) {
      const words = needle.split(/\s+/).filter(w => w.length > 2);
      matched = releaseData.videos.find(v => {
        const vt = v.title.toLowerCase();
        return words.filter(w => vt.includes(w)).length >= Math.ceil(words.length / 2);
      });
    }

    // Last resort: if only one video, use it; otherwise fail
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
    notifySidePanel();

    if (sender?.tab) {
      try { await chrome.sidePanel.open({ tabId: sender.tab.id }); } catch (e) {}
    }

    return { queued: true, item: queueItem };
  },

  'dig:queue-embed': async (data, sender) => {
    let { platform, artist, title, embedUrl, thumb, source_url, tracks } = data;
    if (!embedUrl) return { error: 'No embed URL' };

    // SoundCloud: resolve proper embed URL via oEmbed API
    // The SC widget rejects bare soundcloud.com URLs from chrome-extension context
    if (platform === 'soundcloud' && source_url) {
      try {
        const resolved = await resolveSoundCloudEmbed(source_url);
        if (resolved) embedUrl = resolved;
      } catch (e) {
        // Fall back to the widget URL constructed by content script
      }
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
