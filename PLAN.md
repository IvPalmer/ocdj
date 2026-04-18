# OCDJ — Project Status & Roadmap

## Architecture

Django + React + Docker. Django handles all Python logic natively, React dashboard UI, Docker orchestrates slskd + postgres + backend.

```
ocdj/
├── backend/                     # Django project
│   ├── djtools_project/         # Settings, urls, wsgi
│   ├── core/                    # Config store, health, dashboard stats
│   ├── wanted/                  # Wishlist with multi-source imports
│   ├── soulseek/                # slskd search, matching, downloads
│   ├── recognize/               # Multi-engine mix track identification
│   ├── organize/                # Post-download tagging, renaming, artwork
│   ├── traxdb/                  # Blog scraping + Pixeldrain management
│   └── dig/                     # Browser extension backend (embeds, search)
├── src/                         # React frontend (Vite + TanStack Query)
│   └── components/              # Dashboard, Wanted, Soulseek, TraxDB,
│                                  Recognize, Organize, Settings
├── ocdj-helper/                 # Chrome extension (needs backend)
├── ocdj-helper-standalone/      # Chrome extension (zero-backend, for friends)
├── tools/                       # Legacy CLI tools (traxdb_sync, soulseek_sync)
└── docker-compose.yml           # postgres + django + slskd + frontend
```

### Ports & Services
- Backend: 8002
- Frontend: 5174
- PostgreSQL: 5433
- slskd: 5030/5031

---

## What's Built (production-ready)

### Core
- Key-value config store (DB-backed with env var fallback)
- Health check, dashboard stats, config API
- Auto-resolves playlist names from URLs

### Wanted List
- Full CRUD + bulk operations + pagination
- Import connectors: **Spotify** (OAuth2), **YouTube** (Data API v3), **SoundCloud** (API v2), **Discogs** (wantlist)
- RapidFuzz dedup across all sources
- Import preview with confirmation flow

### Soulseek
- slskd REST integration with rate-limit retries
- Intelligent query generation (catalog # → artist+title → broadened)
- RapidFuzz scoring with catalog/release weighting
- Quality presets (format, bitrate, file size)
- Download progress tracking with path reconstruction

### Recognize (most sophisticated module)
- Multi-engine pipeline: **ACRCloud** (primary, 150M+ fingerprints) → **Shazam** gap-fill (free) → **TrackID.net** merge
- Configurable: 20s ACR step, 8s Shazam gap fill (tuned from testing)
- Description parser extracts embedded tracklists
- Clustering: groups nearby segments, resolves conflicts, deduplicates
- Resume-on-restart for interrupted jobs
- ACRCloud usage tracking + billing estimates
- Add recognized tracks directly to Wanted List

### Organize
- 4-stage pipeline: downloaded → tagged → renamed → ready
- Metadata enrichment from Discogs + MusicBrainz
- ID3/FLAC tag writing via mutagen
- Template-based renaming (configurable)
- Artwork fetching from Discogs + Spotify

### TraxDB
- Blog scraping via external CLI tool (`tools/traxdb_sync/`)
- Pixeldrain download + extraction
- Audit/verification
- Inventory browsing (date folders, file counts)

### Dig (browser extension backend)
- Add items with dedup check (single + batch)
- Discogs video extraction
- Embed proxy for SoundCloud/Spotify/YouTube (CORS bypass)
- YouTube + Bandcamp stream extraction

### Frontend (all 7 sections fully functional)
- Dashboard, Wanted, Soulseek, TraxDB, Recognize, Organize, Settings
- Real-time progress tracking, modals, bulk operations, filtering
- 60+ React Query hooks with smart polling intervals

---

## Recently Completed

### Phase A: Management Commands
- `check_downloads` — polls slskd, updates Download/WantedItem statuses
- `process_wanted` — auto-queues pending wanted items for Soulseek search
- `scrape_traxdb` — triggers blog scrape for cron use
- All support `--dry-run` and `--limit`

### Phase B: Format Conversion
- Conversion stage added to organize pipeline (converting → converted)
- DSL rule parser: `wav -> aiff`, `flac -> aiff`, `mp3>=320k -> keep`, etc.
- FFmpeg conversion with tag + artwork preservation via mutagen
- Conversion rules editor in frontend
- Pipeline stages now: downloaded → tagged → renamed → converted → ready

### Phase C: Bandcamp Import
- Bandcamp scraper (requests + BeautifulSoup, TrAlbum JSON extraction)
- Supports album, track, artist/label, and wishlist/collection URLs
- Full import preview + confirmation flow matching other connectors
- Added to ImportPanel in frontend

### Phase D: End-to-End Pipeline Automation
- `AutoPipeline` orchestrator with `run_automation_cycle()`
- 5 independently toggleable config keys (all opt-in, disabled by default)
- Auto-search, auto-download (configurable confidence threshold), auto-organize
- Management command `run_automation` for cron
- Settings UI with toggles + confidence slider + run/dry-run buttons
- Dashboard pipeline flow visualization (Wanted → Searching → ... → Ready)

### Phase E: Library Section
- `LibraryTrack` model with full metadata + technical info
- Incremental scan of `05_ready/` directory (skips matching mtime)
- Search/filter by artist, title, label, genre, format
- Metadata editing that writes to file via mutagen
- Library stats (counts by format, top genres, total size)
- New `/library` route + sidebar nav item

### Phase F: TraxDB Native Rewrite
- `ScrapedFolder` + `ScrapedTrack` Django models
- Native blog scraper (requests + BeautifulSoup, cookie auth, incremental)
- Native Pixeldrain client (download with retry/resume/range support)
- Batch downloader with dedup + progress tracking
- File audit with size verification
- Browsable scraped archive in frontend
- `tools/traxdb_sync/` preserved but no longer called from Django

### Phase G: Stability & Polish Pass (2026-04-17)

**Soulseek**
- Added timeouts to all slskd HTTP calls (hangs on dead slskd killed)
- Atomic QueueItem + WantedItem status save (`transaction.atomic`)
- Auto-ingest race fixed with `transaction.on_commit`
- `slskd_unreachable` flag surfaced to frontend instead of stuck "queued" state
- `bulk_create` SearchResults (was N+1)
- "Completed, Rejected" now correctly flips to `failed`
- Query simplification: new `simplify_query()` strips hyphens/accents/punctuation, drops noise words (feat/remix/mix/original), caps at 6 tokens — slsk recall now much better
- `search_results_count` via `Count` annotation (was N+1)
- New `DELETE /soulseek/downloads/<id>/` for per-row remove on completed/failed rows
- **New: browse user shared folder** — `GET /soulseek/browse/?username=X&dir_prefix=Y&audio_only=1` with 📁 button in each result row, modal with folder tree, 🔒 lock indicator for privileged-only files and directories (slskd's `lockedDirectories` + `lockedFiles`)
- `SearchResult.is_locked` field + migration 0004

**Frontend resilience**
- `ErrorBoundary` + `ToastProvider` + global mutation/query onError → toast
- `AbortController` 30s timeout on fetch (90s for browse)
- `useDownloadsStatus` signature-diff invalidation (killed refetch storm)
- `useSearchResults` status-keyed + `staleTime: 0` (fixed "No results found" after re-search)

**Recognize**
- `trackid_result` initialized to `None` (fixed AttributeError on lookup failure)

**Organize**
- `try_claim_processing_all()` atomic claim (fixed race)
- `_find_file_on_disk` 20k-entry cap (no more multi-minute hangs on big trees)
- Discogs enrichment fixed: `results[:5]` → `itertools.islice(results, 5)` (was silently crashing on every tag attempt; artwork.py same pattern)

**Wanted**
- Spotify `_status()` distinguishes `auth_failed` from unconfigured

**Backend config**
- `CORS_ALLOWED_ORIGIN_REGEXES` covers `chrome-extension://`, `moz-extension://`, `safari-web-extension://`

**Extension**
- Standalone manifest host_permissions now includes `http://localhost:8002/*`
- **New: Safari extension built** via `safari-web-extension-converter`; Xcode project at `/OCDJ Helper/`, registered with Safari, requires "Allow Unsigned Extensions" until codesigned

**TraxDB**
- Operations list payload 156KB → 1KB (added `TraxDBOperationListSerializer` without `summary`)
- Folder list N+1 → single query (annotate `tracks_count` + `tracks_downloaded`)
- Atomic `_trigger_slot` context manager for trigger views (killed sync/download/audit races)
- **Loud failure** on Google login redirect (was silently returning 0 new lists)
- **New HTML parser fallback** — blog changed format to date-header + `MIRROR1:` lines; parser now regex-scans pixeldrain links and pairs with nearest date
- Downloader trusts DB `pending` folders (was deferring to stale `latest_sync.links_new`)
- Transient download errors (not 404) revert folder to `pending` for next-run retry
- `apps.py` startup hook marks zombie `running` ops as `failed` on every backend boot
- **New tool: `tools/traxdb_sync/refresh_cookies.py`** — one-shot Chrome cookie extractor for stale session refresh
- **Simplified TraxDB UI** — single-flow "Check New Lists → Download N → Advanced" pane replaces old 3-card Sync/Download/Audit layout

**Review**
- Full architecture + UX + strategy review at `docs/REVIEW_2026-04-17.md` (6 parallel agents, 557 lines)
- Panel screenshots at `docs/review_screenshots/`

---

## Phase H — Delivered (2026-04-17)

Shipped this session (H1, H2, H4, H3, H6, H7 quick-wins, H10 + follow-ups).
H5, H8, H9 and the rest of H7 still planned — see sections below.

### New endpoints
- `GET  /api/core/config/schema/` — full schema by category (drives Settings UI)
- `POST /api/core/audit-music-root/` — HTTP wrapper around the mgmt command
- `POST /api/soulseek/connect/` — trigger slskd login to the Soulseek P2P network
- `POST /api/soulseek/disconnect/` — drop the P2P session
- `POST /api/organize/pipeline/rerename/` — re-apply rename template to a stage
- `POST /api/organize/pipeline/retag-clean/` — rewrite ID3/FLAC tags using clean rules; self-heals stale paths via `MUSIC_ROOT` walk
- `POST /api/organize/retag-directory/` — walk a directory, retag every audio file (standalone, no DB)
- `POST /api/library/tracks/<pk>/promote/` — copy a ready track into `REVIEW_FOLDER`
- `PATCH /api/recognize/jobs/<pk>/tracks/<idx>/` — manual track override (`manual=true, verified=true`)

### New things
- **`core.services.config`** — 48-key schema registry with `get_config/set_config/source_of/mask_value`
- **`backend/core/management/commands/audit_music_root.py`** — ID3 folder cleanup (dry-run default, `--apply`, `--reclassify`)
- **Huey worker container** — `worker` service in compose + `huey_volume` + `HUEY` settings block
- **`backend/recognize/tasks.py`, `backend/traxdb/tasks.py`** — `@db_task` wrappers for long jobs
- **`ocdj-sidecar/`** — host-side FastAPI service using `claude-agent-sdk` for the in-app Agent. Max subscription auth via local `claude` CLI — no API key.
- **Agent panel** — `src/components/AgentSection/` chat UI + SSE, 14 tools wired to OCDJ endpoints
- **`REVIEW_FOLDER` config** — promote button copies `05_ready/...` → review staging folder for manual drag into iTunes
- **Orphan scan** — `scan_completed_downloads()` now walks `01_downloaded/` for files without a Download record (Telegram rips, manually-added files) and creates PipelineItems with `download=None`

### Behavior changes
- `run_recognize(job_id)` now enqueues on Huey instead of spawning a raw thread
- Trigger endpoints in `traxdb/views.py` enqueue on Huey (sync/download/audit)
- `write_tags()` always cleans artist/title via the renamer rules before writing — tags match filenames
- `ORGANIZE_RENAME_TEMPLATE` default is now `{artist} - {title}`; artist/title are auto-cleaned of catalog brackets, URL stamps, track prefixes (`A1.`, `B2.`, `01_`, `NN ` when `NN ≤ 30`), `(Original Mix)`/`(Main Mix)`/`(Album Version)`, and artist repetition at the start of the title
- Recognize P0s: `asyncio.run()` reuse crash fixed, gap-fill dedup, Cloudflare cookie refresh, resume validation, hybrid confidence rerank
- Recognize P1s: overlap calc requires ≥70% of both tracks before rejecting; ACRCloud region now config-driven
- Settings panel auto-renders from the schema (13 categories, every key editable)
- Sidebar regrouped: Dashboard / Agent / Capture / Curate / Fetch / Process / Library
- Organize panel shows `final_filename` (current on-disk name) with stale `original_filename` as a subtitle when different
- Wanted "Queue" → **"Find"**, bulk "Add to Queue" → **"Find All"**, empty-state CTAs added
- Soulseek panel now has a **Login** button when `connected && !loggedIn`

### Deferred (still planned)
- H5 Classifier v0 — embeddings + suggestions
- H7 polish remainder — unified StatusBadge, Dashboard recent-activity, Wanted row-expand downstream history, density toggle, shared Pipeline component, Soulseek "Send to Organize" bulk, filename cleanup in Soulseek
- H8 SourceAdapter + scheduler + new sources (SC Likes, YT Watch Later, Shazam history, Safari Tab Group)
- H9 Tests + AutomationTask model

---

## Phase H — Original plan (2026-04-17, post-review)

Full review at `docs/REVIEW_2026-04-17.md` informs this phase. User answers to open questions:
- Personal use, always product-minded → everything configurable (API keys, tokens, playlists, paths)
- TraxDB stays separate archive → future ML classifier surfaces "probably want" tracks
- Music flow: cleaned → Electronic folder → iTunes → manual classification into iTunes playlists
- Recognize target: trackid.net-level accuracy
- Classifier: suggest top-3 + accept, never unattended
- Jobs MUST survive container restart → worker container required

### H1 — Config consolidation (foundation)
- `core.services.config.get_config(key, default, cast)` resolver: DB → env → settings → registry default
- `ConfigKey` registry model: `key, category, type, description, default, is_secret`
- Migrate all hardcoded/env-only: paths (Electronic folder, pipeline root, TraxDB root, 05_ready), API keys, OAuth tokens, ACRCloud region, recognize thresholds, automation confidence, scheduler intervals
- Settings panel auto-renders from registry (tabs by category)

### H2 — ID3 folder cleanup
- Config-driven paths (uses H1) for pipeline root + traxdb root + electronic-library root
- `manage.py audit_music_root` (dry-run) + `--apply` (executes)
- Archive legacy folders to `/Users/palmer/Music/Musicas/Electronic/_archive_2026-04-17/`: `wavs/`, `to fix/`, `rafa/`, `drive-download-…/`, `soulseek_sync/`, `logs/`, `conversion/`, `complete/`, `flacs/`, `sets/`, `downloading/`, `04_ready/` (ghost)
- Sweep 44 orphan root `.flac`s into `01_downloaded/_to_triage/` for reprocessing
- Delete `.venv/`, `.DS_Store`

### H4 — Worker container (jobs survive restart)
- Add **Huey** + SQLite persistence
- Worker container in docker-compose
- Migrate recognize, traxdb downloader, soulseek search, organize batch, wanted sync to Huey tasks
- Remove 13+ raw `threading.Thread` spawn sites

### H3 — Recognize → trackid.net parity
- P0: asyncio.run() reuse (`recognition.py:122`), gap-fill dedup (`pipeline.py:239`), cf_clearance refresh, resume validation, worker crash recovery
- P1: overlap calc (`tt_duration * 0.5` wrong for DJ overlaps), 30s conflict window rejects mashups, ACRCloud region via config
- Manual override: `PATCH /recognize/jobs/<id>/track/<idx>/` with `verified=true` flag
- Hybrid confidence rerank (matched-by-2-engines boost)
- Fingerprint cache: `(audio_hash, segment_index) → result`
- SSE live progress stream
- Per-row "+ Wanted" + bulk "add verified"

### H7 — UX polish + sidebar reorder
- Sidebar groups: Capture (Recognize, TraxDB) / Curate (Wanted) / Fetch (Soulseek) / Process (Organize) / Library (Library, Settings)
- Rename Wanted "Queue" → "Find" with live result count
- Unified `StatusBadge` component
- Empty-state CTAs in Wanted ("Import / TraxDB / Recognize mixtape")
- Dashboard: recent activity feed replaces zero-tiles
- Wanted row-expand shows downstream Soulseek/Organize/Library state
- Density toggle in Library (48px → 32px rows)
- Share Pipeline component between Dashboard and Organize
- Filename cleanup in Soulseek (strip `…www.djsoundtop.com.flac`)
- Move Organize "Conversion Rules" tab into Settings

### H5 — Classifier v0 (suggest+accept)
- New `classify` Django app: `Embedding`, `PlaylistClassifier`, `Classification` models
- Essentia + Discogs-EffNet embedding extractor (Huey task)
- iTunes XML import for training labels
- Per-playlist `LogisticRegression(class_weight='balanced')`
- Triage UI: new track → top-3 playlist suggestions + confidence bars
- **TraxDB extension:** rank un-adopted TraxDB tracks by similarity to adopted library → "probably want" surface in TraxDB panel

### H6 — iTunes bridge (minimal)
- `organize.services.itunes_bridge.add_to_music(path)` via AppleScript
- "Add to iTunes" button on ready tracks
- NO playlist sync, NO ratings, NO play counts

### H8 — SourceAdapter + scheduler + new sources
- `TrackSource` ABC in `wanted/services/base.py`
- Convert 5 existing services (spotify, youtube, soundcloud, discogs, bandcamp) to adapters
- Huey periodic task `sync_wanted_sources` (replaces APScheduler need)
- `poll_interval_hours` + `next_sync_at` on `WantedSource`
- New adapters: SoundCloud Likes, YouTube Watch Later (OAuth), Shazam history, Safari Tab Group (Swift helper app reading `~/Library/Safari/Bookmarks.plist`)
- Cross-source dedup: `source_ids JSONField` on WantedItem

### H9 — Tests + `AutomationTask` unified state
- pytest-django + GH Actions CI
- 5 unit tests: automation FSM, soulseek scoring, organize pipeline stage movement, recognize clustering, cross-module state sync
- `AutomationTask` record-of-truth: OneToOne WantedItem, FKs to SearchQueueItem/Download/PipelineItem/LibraryTrack, `state_history JSONField`
- Wanted row-expand surfaces full downstream history without panel-hopping

### Execution order
H1 → H2 → H4 → H3 → H7 → H5 → H6 → H8 → H9

Rationale: config store first (everything depends on it), disk cleanup motivates path config, worker unblocks durable Recognize P0 fixes, marquee feature next, UX polish, ML moat, iTunes bridge, sources+scheduler, tests last.

---

## Dropped (not planned)

- Telegram import — not needed
- Download verification (AcoustID post-download) — not worth the complexity
- Niche blog scrapers (Dr Banana, Velvet Velour, etc.) — not needed
- Spotify playlist output from Recognize — not needed
- Beets integration — mutagen handles tagging well enough

---

## Reference Docs

- `docs/RESEARCH.md` — Track ID engine research, SoulSync/Tubifarry analysis, architecture decisions
- `docs/SETUP.md` — Legacy CLI tool setup (bootstrap, config, yt-dlp)
