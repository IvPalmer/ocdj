### dj-tools macOS app (SwiftUI)

This repo contains a minimal local SwiftUI app that orchestrates the existing Python tools under `tools/`.

#### Open / run (Xcode)

- Open `app/DJToolsAppXcode/DJToolsApp.xcodeproj` in Xcode.
- Select the `DJToolsApp` scheme and run.

Notes:
- This is a real `.app` bundle with a bundle identifier, which we need for `djtools://oauth/spotify` redirects.
- If Xcode prompts for signing, set your Team in **Signing & Capabilities**.
- Bundle identifier is currently set to `com.palmer.djtools` (change in the target build settings if you prefer).

#### What it does today

- **TraxDB**: runs the repo scripts:
  - `tools/traxdb_sync/run_sync.sh`
  - `tools/traxdb_sync/run_download_bg.sh`
  - `tools/traxdb_sync/run_audit.sh`
- **Soulseek**: runs:
  - `tools/soulseek_sync/run_bg.sh`
  - `tools/soulseek_sync/status.sh`
- **Recognize (MVP)**: downloads and normalizes audio from a URL (YouTube/SoundCloud) using:
  - `tools/bin/yt-dlp`
  - `tools/bin/ffmpeg`
  - output is written to `logs/recognize/<timestamp>/normalized.wav`

#### Required setup

From repo root:

```bash
bash bootstrap.sh
bash tools/setup_media_tools.sh
```

Then ensure `djtools_config.json` is filled out (Pixeldrain/slskd + Spotify/Telegram as needed).

#### Artifacts

All logs/reports/job state are stored under:

- `repo/logs/`

Downloads (music files) still go to your ID3 folder (configured by `soulseek_root`, `traxdb-root`, etc).


