## dj-tools (macOS app) — Implementation Plan

### Goal
One native **macOS SwiftUI** application that automates three workflows:

- **TraxDB → Pixeldrain**: scrape TraxDB posts, generate a report, download missing Pixeldrain lists, audit completeness.
- **Soulseek via slskd**: manage the slskd service (Docker), run searches from `wanted.txt`, enqueue downloads with strict format policy.
- **DJ-set recognition → Spotify playlist → Telegram DM**: recognize tracks from a set URL (YouTube/SoundCloud/etc) using **ShazamKit**, create a Spotify playlist, DM the playlist link + tracklist to Telegram.

Constraints:
- Runs on the user’s **Mac**.
- **No external recognition provider fallback** initially (ShazamKit only).
- Keep current CLI tools usable; UI orchestrates them.

---

### Repo layout (new home)
Location:
- `/Users/palmer/Dev/dj-tools/`

Structure:
- `app/`
  - SwiftUI macOS application (Xcode project)
  - Responsible for UI, job orchestration, credentials, and calling tools
- `tools/`
  - Existing Python tools, treated as “managed utilities”
  - `tools/traxdb_sync/`
  - `tools/soulseek_sync/`
- `docs/`
  - Design docs, screenshots, notes
- `IMPLEMENTATION_PLAN.md` (this file)

Optional later:
- `shared/` for shared schemas (JSON), OpenAPI, or a small Swift/Python shared model.

---

### Runtime architecture

#### 1) The macOS app is the orchestrator
The app owns:
- Job creation + job history
- Service management (slskd via Docker)
- Configuration + secrets (Keychain)
- UI presentation of progress/logs

#### 2) Python tools remain the workhorses (phase 1)
Initially, the SwiftUI app calls the Python entrypoints as subprocesses:
- TraxDB:
  - `python3 tools/traxdb_sync/sync.py --config ...`
  - `bash tools/traxdb_sync/run_download_bg.sh`
  - `python3 tools/traxdb_sync/audit.py ...`
- Soulseek:
  - `bash tools/soulseek_sync/run_now.sh` / `run_bg.sh` / `status.sh`

Benefits:
- Very fast integration.
- Reuses existing, debugged logic.
- Keeps CLI usable.

Later (phase 2+), you can replace subprocess calls with:
- A local FastAPI server, or
- Direct Swift rewrites of specific pieces.

---

### Data & state

#### App state (jobs, history)
Use SQLite via Swift (`GRDB` or `SQLite.swift`) or CoreData.

Tables (suggested):
- `jobs`:
  - `id`, `type` (`traxdb|soulseek|recognize`), `status`, `created_at`, `started_at`, `ended_at`, `params_json`, `artifacts_json`
- `job_events` (optional):
  - structured events for progress timelines

#### Logs & artifacts
- Store logs under a single folder, configurable:
  - e.g. `~/Library/Application Support/dj-tools/logs/`
- Each job keeps links to:
  - progress JSON
  - final report JSON
  - log file

The existing tools already write to `logs/` relative to their repo root; in phase 1, we can:
- run them with `cwd=/Users/palmer/Dev/dj-tools` so logs consolidate.

---

### Config & secrets

#### Phase 1 (simple)
- Keep tool configs as they are (absolute paths are fine):
  - `tools/traxdb_sync/config.json`
  - `tools/soulseek_sync/config.json`

#### Phase 2 (app-managed)
- Move secrets into Keychain:
  - Pixeldrain API key
  - slskd API key
  - Spotify refresh token
  - Telegram bot token
- The app writes minimal, non-secret config files:
  - file paths, roots, UI preferences

---

### slskd integration (managed service)

Approach: **Docker Compose managed by the app**.

App actions:
- Start: `docker compose -f tools/soulseek_sync/slskd-compose.yml up -d`
- Stop: `docker compose -f ... down`
- Restart: `docker compose -f ... restart`

Health checks:
- HTTP ping `http://localhost:5030/`
- API ping using X-API-Key
- Server state (connected/logged in)

UI:
- Toggle start/stop
- Show queue summary (queued/in-progress/completed/rejected)
- Open slskd web UI

---

### ShazamKit integration (recognition)

Because the app is native macOS, we run ShazamKit directly in Swift.

Pipeline:
1) **Ingest** a set URL (YouTube/SoundCloud/etc)
   - Use `yt-dlp` (bundled or user-installed) to extract audio to a temp folder
2) **Normalize** audio
   - Convert to consistent format via `ffmpeg` (bundled or user-installed)
3) **Segment**
   - Sample ~15s clips every 30–60s
   - Keep timestamps
4) **Match** with ShazamKit
   - Use `SHSession` to match audio buffers
   - Collect candidate media items + confidence signals
5) **Merge**
   - Deduplicate consecutive matches
   - Produce an ordered tracklist with approximate timestamps

Artifacts:
- `tracklist.json` (ordered tracks + timestamps + any confidence/metadata)

---

### Spotify playlist creation

Auth:
- Spotify Authorization Code flow (single user)
- Store refresh token in Keychain

Flow:
- Create playlist (name based on set title/date)
- Search each recognized track
- Pick best match (strict-ish matching, then fallback scoring)
- Add tracks in order

Artifacts:
- playlist URL
- list of matched/unmatched tracks

---

### Telegram DM

Use Telegram Bot API:
- Bot token in Keychain
- Your `chat_id` stored after a one-time “pairing” step (send /start to bot)

Message:
- Spotify playlist link
- Tracklist (timestamped)
- Unmatched section

---

### UI plan (SwiftUI screens)

#### Home / Dashboard
- Buttons for: TraxDB, Soulseek, Recognize
- Recent jobs list

#### TraxDB
- Config summary (traxdb root, cookies, start URL)
- Actions:
  - “Generate report”
  - “Download missing (background)”
  - “Audit”
- Show latest report link + summary

#### Soulseek
- slskd service controls (start/stop/restart)
- Queue summary + refresh
- Actions:
  - “Run wanted.txt now”
  - “Run in background”
- Open slskd web UI

#### Recognize
- Input: set URL
- Options: segment interval, clip length, max duration
- “Recognize → Create Spotify playlist → DM to Telegram”
- Show generated tracklist + playlist URL

#### Jobs
- Job list
- Click a job:
  - view log
  - view progress JSON
  - view final report

---

### Implementation milestones (practical order)

#### Milestone A — Repo + tool wiring (1–2 days)
- Create Xcode SwiftUI app scaffold in `app/`
- Add a simple “Run command” wrapper in Swift (Process/pipe)
- Add screens for:
  - TraxDB: run `sync.py` and show report path
  - Soulseek: start/stop slskd + show status output

#### Milestone B — Spotify + Telegram plumbing (1–2 days)
- Implement Spotify OAuth + playlist create
- Implement Telegram bot pairing + DM

#### Milestone C — ShazamKit recognition MVP (2–4 days)
- Ingest audio from URL (yt-dlp)
- Segment and match with ShazamKit
- Produce tracklist

#### Milestone D — End-to-end “Recognize → Playlist → DM” (1–2 days)
- Connect recognition output to Spotify search/add
- Send Telegram message

#### Milestone E — Polish + reliability (ongoing)
- Better matching heuristics
- Better segmentation around transitions
- Robust retries + rate limiting
- One unified logs/artifacts location

---

### External dependencies (Mac)
- Docker Desktop (for slskd management)
- `yt-dlp` (for set audio extraction)
- `ffmpeg` (for audio conversion)

We can add an onboarding screen that checks these and explains how to install (Homebrew).
