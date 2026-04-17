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
