# OCDJ Standalone — Browser Extension for DJs

Zero-backend Chrome extension that adds **Wantlist** and **Queue + Player** buttons to Discogs, Bandcamp, YouTube, SoundCloud, and Spotify.

No server, no account, no setup — everything stored locally in your browser.

## Install

1. Download and unzip `ocdj-standalone.zip`
2. Open Chrome and go to `chrome://extensions/`
3. Enable **Developer mode** (toggle in the top-right corner)
4. Click **Load unpacked** and select the unzipped folder
5. Pin the extension to your toolbar for quick access

Works on Chrome 116+ (macOS, Windows, Linux) and Chromium-based browsers (Edge, Brave, Arc, Opera).

## Features

### Wantlist (persistent)
- Shovel button on every supported site saves artist, title, label, and source link
- View, search, and manage your wantlist from the extension popup
- Export as `.txt` file / import from file
- Duplicate detection by artist+title and source URL
- Survives browser restarts — stored in Chrome's local storage

### Queue + Side Panel Player (persistent)
- Play button sends releases/tracks to the side panel player
- Plays via native embeds (YouTube, SoundCloud, Bandcamp, Spotify)
- Queue and playback position persist between browser sessions
- Per-track wantlist buttons directly in the queue
- Next track, next release, clear queue controls

### Platform Support

| Platform | Where buttons appear | Player | How it works |
|----------|---------------------|--------|-------------|
| **Discogs** | Release pages (header + every track), label/artist pages, marketplace, search results | YouTube embed | Fetches YouTube videos via Discogs API; per-track matches by title |
| **YouTube** | Video pages, playlist pages | YouTube embed | youtube-nocookie.com with Referer fix |
| **SoundCloud** | Track pages, set/playlist pages, per-track in sets | SoundCloud widget | Extracts API track ID from page; widget with referrer override |
| **Bandcamp** | Album pages, track pages | Bandcamp embed | Native embedded player |
| **Spotify** | Track pages, album pages | Spotify embed | 30s previews unless logged into Spotify |

## Settings

Right-click extension icon > **Options**, or click the gear icon in the popup:

- **Discogs API Token** — Optional. 60 req/min without, 240/min with. Get one at https://www.discogs.com/settings/developers
- **Per-site toggles** — Enable/disable buttons per platform
- **Toast notifications** — Toggle confirmation toasts

## Limitations

- **No backend** — Everything runs in your browser. No Soulseek search, track recognition, file organization, or any server-side feature.
- **No cross-device sync** — Wantlist and queue are per Chrome profile. Use export/import to transfer.
- **SoundCloud private tracks** — Won't play (requires auth tokens the extension doesn't have).
- **Spotify playback** — 30-second previews unless you're logged into Spotify in the same browser.
- **Chrome only** — No Firefox or Safari (MV3 side panel API is Chrome-specific).

## Architecture

```
ocdj-helper-standalone/
├── manifest.json                 # MV3 manifest
├── background/service-worker.js  # Message routing, Discogs API, storage, declarativeNetRequest
├── content/
│   ├── shared.js                 # Button factory, toasts, DOM observer, sendToBackground
│   ├── discogs.js                # Release, tracklist, label, marketplace, search injection
│   ├── bandcamp.js               # Album + track page injection
│   ├── youtube.js                # Video + playlist page injection
│   ├── soundcloud.js             # Track + set page injection (with API URL extraction)
│   ├── spotify.js                # Track + album page injection
│   └── sc-widget-fix.js          # MAIN world script — overrides document.referrer for SC widget
├── popup/                        # Wantlist viewer + search + export/import
├── side-panel/                   # Queue player with multi-platform embeds
├── options/                      # Settings (Discogs token, site toggles, toasts)
├── styles/injected.css           # Injected button/toast styles
└── icons/                        # Extension icons (16, 48, 128px)
```

## Uninstall

Go to `chrome://extensions/`, find OCDJ Standalone, and click **Remove**. All local data is deleted with it.
