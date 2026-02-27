# DJ Tools v2 — Rebuild Plan

## Architecture Decision: Django + React + Docker (Vault Pattern)

Matching your Vault project structure. Django handles all the heavy Python logic natively (no subprocess hacks), React gives you the dashboard UI, Docker keeps slskd + postgres + backend orchestrated.

```
dj-tools/
├── backend/                     # Django project
│   ├── djtools_project/         # Django settings, urls, wsgi
│   ├── core/                    # Django app: shared models, config
│   │   ├── models.py            # WantedTrack, Source, Config
│   │   └── services.py          # Config loader, shared utils
│   ├── traxdb/                  # Django app: blog scraping
│   │   ├── models.py            # ScrapedFolder, ScrapedTrack
│   │   ├── services.py          # Incremental scraper logic
│   │   └── views.py             # API endpoints
│   ├── soulseek/                # Django app: slskd integration
│   │   ├── models.py            # SearchResult, Download, QualityPreset
│   │   ├── services.py          # Matching engine, download orchestrator
│   │   └── views.py             # API endpoints
│   ├── recognize/               # Django app: track identification
│   │   ├── models.py            # IdentificationResult
│   │   ├── services.py          # AcoustID pipeline, mix segmentation
│   │   └── views.py             # API endpoints
│   ├── library/                 # Django app: post-processing
│   │   ├── models.py            # LibraryTrack, ConversionRule
│   │   ├── services.py          # Beets integration, FFmpeg conversion
│   │   └── views.py             # API endpoints
│   ├── wanted/                  # Django app: wishlist management
│   │   ├── models.py            # WantedItem, WantedSource
│   │   ├── services.py          # Want pipeline (identify → search → download)
│   │   └── views.py             # API endpoints
│   ├── manage.py
│   ├── requirements.txt
│   └── Dockerfile
├── src/                         # React frontend (Vite)
│   ├── api/                     # API client (TanStack Query)
│   ├── components/
│   │   ├── Layout.jsx           # Sidebar nav (like Vault)
│   │   ├── DashboardSection/    # Overview stats
│   │   ├── WantedSection/       # Wishlist manager
│   │   ├── TraxDBSection/       # Blog scraping controls
│   │   ├── SoulseekSection/     # Search + download monitor
│   │   ├── RecognizeSection/    # Track ID + mix analysis
│   │   ├── LibrarySection/      # File browser, convert, tag
│   │   └── SettingsSection/     # Config, quality presets
│   ├── App.jsx
│   └── main.jsx
├── docker-compose.yml           # postgres + django + slskd + react
├── dev.sh                       # One-command dev startup (Vault pattern)
├── package.json
├── vite.config.js
└── README.md
```

### Why This Works

1. **All Python logic is native Django** — no subprocess calls to Python scripts. The matching engine, scraper, AcoustID integration, FFmpeg calls, Beets CLI — all run in Django services.
2. **Same stack as Vault** — you know how to maintain it, same deployment pattern, same debugging workflow.
3. **Docker-compose orchestrates slskd** — slskd runs as a sibling container. Django talks to it via REST API (`slskd-api` package). No manual Docker management in the app.
4. **React frontend is thin** — just displays data, triggers actions, shows progress. All logic lives in Django.
5. **PostgreSQL for structured data** — wanted list, download history, library metadata, scrape state. Way better than JSON files.

### Decisions Made

- **Backend port**: 8002 (avoids conflict with Vault's 8001)
- **Frontend port**: 5174 (avoids Vault's 5173/5175)
- **slskd**: Inside docker-compose (managed, not standalone)
- **Want sources**: Connectors for Spotify/Telegram/SoundCloud configured but manual trigger only — no auto-import until you define org structure
- **Task runner**: Management commands + cron (simple, no Celery/Redis overhead)

### docker-compose.yml Shape

```yaml
services:
  db:
    image: postgres:15
    volumes: [postgres_data:/var/lib/postgresql/data/]
    environment:
      POSTGRES_DB: djtools
      POSTGRES_USER: djtools_user
      POSTGRES_PASSWORD: djtools_password
    ports: ["5433:5432"]  # 5433 to avoid conflict with Vault's postgres
    restart: always

  slskd:
    image: slskd/slskd:latest
    ports: ["5030:5030", "5031:5031"]
    volumes:
      - slskd_data:/app
      - /Users/palmer/Music/Musicas:/music
    environment:
      SLSKD_REMOTE_CONFIGURATION: "true"
      SLSKD_SLSK_LISTEN_PORT: 50300
    restart: always

  backend:
    build: ./backend
    depends_on: [db, slskd]
    ports: ["8002:8002"]
    volumes:
      - ./backend:/app
      - /Users/palmer/Music/Musicas:/music
    environment:
      DEBUG: "1"
      POSTGRES_DB: djtools
      POSTGRES_USER: djtools_user
      POSTGRES_PASSWORD: djtools_password
      POSTGRES_HOST: db
      SLSKD_BASE_URL: "http://slskd:5030"
      SLSKD_API_KEY: "${SLSKD_API_KEY}"
      ACOUSTID_API_KEY: "${ACOUSTID_API_KEY}"
    command: python manage.py runserver 0.0.0.0:8002
    restart: always

  frontend:
    image: node:22-alpine
    working_dir: /app
    ports: ["5174:5174"]
    volumes:
      - .:/app
      - node_modules:/app/node_modules
    command: npm run dev -- --host --port 5174
    depends_on: [backend]
    restart: unless-stopped

volumes:
  postgres_data:
  slskd_data:
  node_modules:
```

---

## Phase 1: Foundation (migrate + wanted list + basic slskd)

### 1.1 Project Scaffold
- Django project with apps: `core`, `wanted`, `soulseek`
- React frontend with Vite + TanStack Query (Vault pattern)
- docker-compose with postgres + slskd + backend + frontend
- `dev.sh` one-command startup
- Migrate `djtools_config.json` into Django settings/env vars

### 1.2 Wanted List (the central hub)
**Models:**
```python
class WantedSource(models.Model):
    name = models.CharField()          # "dr-banana", "velvet-velour"
    url = models.URLField(blank=True)
    source_type = models.CharField()    # blog, soundcloud, youtube, manual
    last_checked = models.DateTimeField(null=True)

class WantedItem(models.Model):
    artist = models.CharField(blank=True)
    title = models.CharField(blank=True)
    source = models.ForeignKey(WantedSource)
    notes = models.TextField(blank=True)  # "heard in Feb mix at 23:15"
    status = models.CharField()  # pending, identified, searching, downloading, downloaded, failed
    identified_via = models.CharField(null=True)  # acoustid, manual
    acoustid_fingerprint = models.TextField(null=True)
    file_path = models.CharField(null=True)
    added = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
```

**UI**: Table with filters (by source, status), add form, bulk actions, status badges.

### 1.3 Soulseek Integration (basic)
- Connect to slskd via `slskd-api` Python package
- Simple search: take WantedItem artist+title → search slskd → show results
- Manual download trigger from search results
- Download progress monitoring via slskd API polling

---

## Phase 2: Smart Matching (SoulSync patterns)

### 2.1 Matching Engine
Reimplement SoulSync's core patterns using `rapidfuzz`:

```python
# services.py (soulseek app)

def normalize_query(artist, title):
    """Strip feat/ft, parenthetical info, unicode normalize"""

def generate_queries(artist, title):
    """Generate 2-3 search variations"""
    # 1. "artist title" (exact)
    # 2. "artist" only (broader)
    # 3. title words only (broadest fallback)

def score_result(wanted, result):
    """Weighted fuzzy score: artist match + title match + format bonus"""

def filter_results(results, quality_prefs):
    """Apply format/bitrate/size preferences"""
```

Start with this simple version. Add remix detection, VA handling etc. only when we see real failures.

### 2.2 Quality Preferences
```python
class QualityPreset(models.Model):
    name = models.CharField()  # "DJ Ready", "Archive", "Space Saver"
    preferred_formats = JSONField()  # ["flac", "wav", "aiff", "mp3"]
    min_bitrate = models.IntegerField(default=256)
    max_file_size_mb = models.IntegerField(default=200)
    min_file_size_mb = models.IntegerField(default=2)
```

### 2.3 Auto-Pipeline
WantedItem status flow:
```
pending → searching → found → downloading → downloaded → tagged → organized
                    → not_found (retry later)
```

Background tasks via Django management commands (run manually or via cron):

```bash
# Process all pending wanted items
python manage.py process_wanted

# Scrape TraxDB for new folders
python manage.py scrape_traxdb

# Check download progress and update statuses
python manage.py check_downloads
```

Crontab example:
```
*/30 * * * * cd /path/to/backend && python manage.py process_wanted
0 */6 * * *  cd /path/to/backend && python manage.py scrape_traxdb
*/5 * * * *  cd /path/to/backend && python manage.py check_downloads
```

---

## Phase 3: Track Identification (Chromaprint/AcoustID)

### 3.1 AcoustID Integration
```python
# recognize/services.py
import acoustid

def identify_file(audio_path):
    """Fingerprint local file and lookup via AcoustID"""
    results = acoustid.match(ACOUSTID_API_KEY, audio_path)
    # Returns MusicBrainz recording IDs, artist, title

def identify_from_url(url):
    """Download via yt-dlp, segment, identify each chunk"""
    # 1. yt-dlp download
    # 2. FFmpeg split into segments
    # 3. AcoustID lookup each segment
    # 4. Return timestamped tracklist
```

### 3.2 Mix Tracklist Extraction (trackid.net-style)
- Input: YouTube/SoundCloud URL
- Download audio → segment into chunks (overlap for transition detection)
- Run each chunk through AcoustID
- Output: timestamped tracklist with confidence scores
- Unidentified segments flagged for manual input
- "Add all to wanted list" button

### 3.3 Download Verification
After downloading from Soulseek, optionally verify with AcoustID that the file matches what we expected (SoulSync's fail-open pattern).

---

## Phase 4: TraxDB Rewrite

### 4.1 Incremental Scraping
```python
class ScrapedFolder(models.Model):
    folder_id = models.CharField(unique=True)
    title = models.CharField()
    url = models.URLField()
    scraped_at = models.DateTimeField(auto_now_add=True)

class ScrapedTrack(models.Model):
    folder = models.ForeignKey(ScrapedFolder)
    filename = models.CharField()
    download_url = models.URLField()
    downloaded = models.BooleanField(default=False)
```

- Store every scraped folder in DB
- On new scrape: start from newest, stop when hitting already-known folder
- Only download new tracks from new folders
- Track download status per file

---

## Phase 5: Post-Processing Pipeline

### 5.1 Format Conversion (Tubifarry-inspired DSL)
```python
CONVERSION_RULES = [
    "wav -> aiff",
    "flac -> aiff",
    "mp3>=320k -> keep",      # high bitrate MP3 is fine
    "mp3<320k -> skip",       # low bitrate skip
]
```
FFmpeg subprocess for actual conversion. Mutagen for metadata preservation.

### 5.2 Beets Integration
Post-download auto-tagging:
```bash
beet import /path/to/downloads --quiet
```
Or use Beets' Python API for tighter integration:
- Auto-tag via MusicBrainz
- Fetch album art
- Apply file organization template
- Write tags

### 5.3 File Organization
Template system (SoulSync-inspired):
```
/Music/Electronic/{artist}/{artist} - {title}.{ext}
```

---

## Phase 6: Niche Source Scrapers

### 6.1 Blog/Channel Scrapers
Custom scrapers for your specific sources:
- Dr Banana
- Velvet Velour
- Alec Falconer
- Other niche sources

Each scraper: fetch new content → extract track info → add to wanted list.

---

## Migration Path (from current Swift app)

### What We Keep
- `djtools_config.json` values → Django env vars / settings
- `tools/traxdb_sync/` logic → rewrite in Django `traxdb` app
- `tools/soulseek_sync/` logic → rewrite in Django `soulseek` app
- Existing slskd Docker config
- yt-dlp + ffmpeg binaries (called from Django via subprocess)
- Spotify OAuth flow (Django handles this natively with `requests`)
- Telegram bot integration (Django service)

### What We Drop
- Swift/SwiftUI app entirely
- ShazamKit
- ProcessRunner subprocess architecture
- Package.swift

### What's New
- PostgreSQL for all state (replaces JSON files)
- Django REST API (replaces Swift -> Python subprocess)
- React dashboard (replaces SwiftUI)
- Chromaprint/AcoustID (replaces ShazamKit)
- slskd-api Python package (replaces manual slskd subprocess calls)
- Beets for metadata (replaces nothing — new capability)

---

## Want Source Connectors (configured, manual trigger)

Each connector can import tracks into the wanted list on demand (button press, not automatic):

| Connector | How | Status |
|-----------|-----|--------|
| **Manual** | Add form in UI | Phase 1 |
| **TraxDB** | Scrape blog, extract tracks | Phase 4 |
| **Spotify** | Import from playlist or liked songs | Phase 3+ |
| **Telegram** | Import from saved messages / channels | Phase 3+ |
| **SoundCloud** | Import from likes / reposts | Future |
| **YouTube** | Extract tracklist from mix URL | Phase 3 |
| **Dr Banana / Velvet Velour / etc** | Custom scrapers | Phase 6 |

Each connector writes `WantedItem` rows with the source tagged. You decide when to trigger each one and how to organize the results.

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Soulseek matching produces bad results | Start minimal (Phase 2), log all misses, iterate on scoring |
| AcoustID doesn't recognize underground tracks | Expected — it's free + best-effort. Log unmatched for manual ID |
| Scope creep (6 phases is a lot) | Phase 1-2 are the MVP. Everything else is incremental. Ship after Phase 2 |
| Docker complexity | Copy Vault's proven docker-compose pattern exactly |
| Beets integration friction | Phase 5 is optional — Mutagen alone handles basic tagging |
