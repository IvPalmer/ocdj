# OCDJ Standalone — Chrome Extension for DJs

Zero-backend Chrome extension that adds **Wantlist** and **Queue + Player** buttons to Discogs, Bandcamp, YouTube, SoundCloud, and Spotify.

No server, no account, no Docker — everything stored locally in the browser.

## Features

### Wantlist
- "Dig" button on every platform saves artist, title, label, and source link
- View, search, and manage your wantlist from the extension popup
- Export as `.txt` file
- Exact duplicate detection by artist+title and source URL

### Queue + Player
- "Queue" button sends releases to the side panel player
- Inline playback via native embeds (no backend proxy needed)
- Queue persists between browser sessions
- Per-track wantlist buttons in the queue

### Platform Support

| Platform | Wantlist | Player | How |
|----------|----------|--------|-----|
| Discogs | Releases, tracklists, labels, marketplace, search | YouTube embed | Discogs API → extracts YouTube videos |
| Bandcamp | Albums, tracks, labels | Bandcamp embed | Native embedded player |
| YouTube | Videos | YouTube embed | youtube-nocookie.com with Referer fix |
| SoundCloud | Tracks, sets, per-track in sets | SoundCloud widget | Widget with document.referrer override |
| Spotify | Tracks, albums, playlists | Spotify embed | Native embedded player |

## Install

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top right toggle)
3. Click **Load unpacked**
4. Select this `extension-standalone/` folder

Works on Chrome, Edge, Brave, Arc, and any Chromium browser.

## Optional Settings

Click the extension icon → gear icon → Settings:

- **Discogs Personal Access Token** — increases Discogs API rate limit from 60 to 240 req/min
- **Per-site toggles** — enable/disable content scripts per platform
- **Toast notifications** — show/hide save confirmations

## Architecture

```
extension-standalone/
├── manifest.json                 # MV3 manifest
├── background/service-worker.js  # Message routing, Discogs API, storage, declarativeNetRequest
├── content/
│   ├── shared.js                 # Button factory, toasts, DOM observer, sendToBackground
│   ├── discogs.js                # Discogs content script
│   ├── bandcamp.js               # Bandcamp content script
│   ├── youtube.js                # YouTube content script
│   ├── soundcloud.js             # SoundCloud content script
│   ├── spotify.js                # Spotify content script
│   └── sc-widget-fix.js          # MAIN world script — overrides document.referrer for SC widget
├── popup/                        # Wantlist viewer + search + export
├── side-panel/                   # Queue player with multi-platform embeds
├── options/                      # Settings page (Discogs token, site toggles)
├── styles/injected.css           # Injected button/toast styles
└── icons/                        # Extension icons
```

### Technical Notes

- **YouTube Error 153 fix**: `declarativeNetRequest` sets `Referer: https://www.google.com/` on YouTube embed requests, bypassing the chrome-extension:// origin block.
- **SoundCloud widget fix**: Content script injected at `document_start` in `MAIN` world overrides `document.referrer` to return `https://soundcloud.com/`, so the widget accepts the embedding context.
- **SoundCloud oEmbed**: Service worker resolves SoundCloud URLs via the oEmbed API to get proper `api.soundcloud.com` embed URLs.
- **No backend**: All data in `chrome.storage.local`. Discogs API called directly from service worker. All embeds loaded directly (YouTube, Bandcamp, SoundCloud, Spotify).

## Differences from Full OCDJ Extension

This standalone version removes features that require the OCDJ Django backend:

- No "Recognize" button on YouTube (needs audio fingerprinting backend)
- No "Import Playlist" on YouTube/SoundCloud/Spotify (needs backend import pipeline)
- No programmatic play/pause (embeds control themselves)
- No fuzzy duplicate detection (exact match only)
- No per-track YouTube search for SoundCloud sets
- No Bandcamp custom audio player (uses native Bandcamp embed instead)
