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

## Remaining Work (ordered easy → hard)

### Phase A: Management Commands (easy)
Add Django management commands for cron automation. Each wraps existing service logic.
- `check_downloads` — poll slskd for completed downloads, update statuses, ingest into organize pipeline
- `process_wanted` — auto-queue pending wanted items for Soulseek search
- `scrape_traxdb` — trigger TraxDB blog scrape for new Pixeldrain lists
- Crontab example in docs

### Phase B: Format Conversion (easy-medium)
Add a conversion step to the organize pipeline between "renamed" and "ready".
- Conversion rules config (Tubifarry DSL style): `wav → aiff`, `flac → aiff`, `mp3>=320k → keep`
- FFmpeg subprocess for actual conversion
- Preserve metadata through conversion (mutagen re-tag after convert)
- Settings UI for conversion rules
- New pipeline stage: `downloaded → tagged → renamed → converted → ready`

### Phase C: Bandcamp Import (medium)
Add Bandcamp as a wanted list import source, matching existing Spotify/YouTube/SoundCloud/Discogs pattern.
- Bandcamp collection/wishlist scraping (yt-dlp or direct scrape)
- Label page parsing (all releases from a label)
- Import preview + confirmation flow (same pattern as other connectors)
- Frontend: add Bandcamp option to ImportPanel

### Phase D: End-to-End Pipeline Automation (medium)
Connect the manual steps into an optional auto-pipeline flow.
- Wanted item status machine: `pending → searching → found → downloading → downloaded → tagged → renamed → ready`
- Auto-advance: when download completes → trigger organize processing
- Auto-advance: when search finds high-confidence match → auto-download (configurable threshold)
- Pipeline status view showing items across all stages
- Manual override at any step

### Phase E: Library Section (medium-hard)
Add a library browser for files that have passed through the organize pipeline.
- Browse `04_ready/` directory tree
- Search/filter by artist, title, label, genre, format
- Metadata viewer + editor
- File stats (format, bitrate, duration, size)
- Frontend: new Library section in sidebar

### Phase F: TraxDB Native Rewrite (hard)
Migrate TraxDB from external CLI tool to native Django services.
- `ScrapedFolder` + `ScrapedTrack` Django models
- Native Pixeldrain client (replace external `pixeldrain.py`)
- Incremental scraping with proper ORM state tracking
- Per-file download status tracking
- Remove dependency on `tools/traxdb_sync/`

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
