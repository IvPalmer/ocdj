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

## What's Not Built

### Not started
- **Telegram import** — env vars configured, no service implementation
- **Bandcamp full import** — stream extraction only, no playlist import
- **Beets integration** — not wired in (mutagen handles tagging directly)
- **Format conversion pipeline** — no wav→aiff/flac conversion rules (Tubifarry DSL idea from research)
- **Niche blog scrapers** — Dr Banana, Velvet Velour, Alec Falconer (Phase 6 in original plan)
- **Django management commands** — no `process_wanted`, `scrape_traxdb`, `check_downloads` for cron
- **Download verification** — AcoustID post-download file verification

### Partially done
- **MusicBrainz** — library imported in organize, used for metadata lookup, but not deeply integrated
- **TraxDB** — works but delegates to external CLI tool rather than native Django services

---

## Reference Docs

- `docs/RESEARCH.md` — Track ID engine research, SoulSync/Tubifarry analysis, architecture decisions
- `docs/SETUP.md` — Legacy CLI tool setup (bootstrap, config, yt-dlp)
