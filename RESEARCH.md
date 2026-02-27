# DJ Tools Research Report v2

## 1. Track ID — The Real Options for Underground Electronic

### The Problem
ShazamKit failed hard — 0 matches on a full scan. For niche electronic (Dr Banana, Velvet Velour, Alec Falconer type content), commercial databases are thin. Shazam bought Beatport's catalog to tag subgenres, but underground white labels, small-run vinyl rips, and SoundCloud-only releases still fall through.

### AudD Deep Dive

**What it is**: Commercial music recognition API, 80M+ song database, neural-network fingerprinting.

**Pricing**:
- **Free tier**: 300 requests total (not per month — total lifetime)
- **Paid**: $2–$5 per 1,000 requests depending on volume
- 100K requests/mo = $450, 500K/mo = $1,800

**Electronic music accuracy**: AudD claims 99.5% accuracy but that's on mainstream catalog. For underground electronic, expect significantly worse — these services depend on having the track in their database. If a track exists only on a Bandcamp page or a SoundCloud upload, neither AudD nor Shazam will find it. The 80M track database skews pop/rock/hip-hop.

**Verdict for us**: AudD is marginally better than ShazamKit (bigger DB, 300ms vs slow ShazamKit processing) but **not a silver bullet for underground electronic**. The 300 free requests are basically a trial, not a usable free tier.

Source: [AudD API](https://audd.io/), [AudD Docs](https://docs.audd.io/)

### Free Alternative: Chromaprint + AcoustID

**What it is**: Fully open source fingerprinting (Chromaprint C library) + free web service (AcoustID) with 30M+ fingerprints linked to MusicBrainz.

**Why it's better for us**:
- **Completely free** — no request limits, open source
- Python bindings: [`pyacoustid`](https://pypi.org/project/pyacoustid/) wraps both fingerprinting and web service lookup
- Beets has a [chroma plugin](https://beets.readthedocs.io/en/stable/plugins/chroma.html) that uses it natively
- MusicBrainz has better underground coverage than commercial DBs (community-contributed)
- SoulSync already uses AcoustID for download verification

**Limitation**: Only identifies tracks that exist in MusicBrainz/AcoustID database. Still won't find truly obscure unreleased stuff.

Source: [AcoustID](https://acoustid.org/), [Chromaprint](https://acoustid.org/chromaprint), [pyacoustid](https://github.com/beetbox/pyacoustid)

### TrackID.net Model (Your Inspiration)

[TrackID.net](https://trackid.net/) extracts tracklists from SoundCloud, Mixcloud, YouTube mixes. Premium users can submit links and get auto-generated tracklists within hours. Related tools:
- [trackid.dev](https://trackid.dev/) — paste YouTube link, get timestamped tracklist (free)
- [mixtape.dj](https://mixtape.dj/) — automatic tracklist identifier
- [1001Tracklists](https://www.1001tracklists.com/) — community DB of DJ set tracklists

**How they work**: They chop the audio into segments and run each through fingerprinting (likely ACRCloud or similar commercial API). The premium model offloads cost to subscribers.

**For our app**: We could build a similar flow:
1. Download audio via yt-dlp (already have this)
2. Segment into chunks (already doing this in RecognizeModel)
3. Run each chunk through AcoustID (free) → then AudD as paid fallback
4. Output timestamped tracklist
5. Allow manual correction for unidentified segments

### Recommendation
**Primary**: Chromaprint/AcoustID (free, unlimited, open source)
**Fallback**: AudD (300 free, then paid — only for tracks AcoustID misses)
**Kill**: ShazamKit entirely

---

## 2. Lidarr — Not for Underground Electronic

### Why Lidarr Doesn't Work for Us

Lidarr depends entirely on MusicBrainz for its catalog. This creates hard limitations:

- **Electronic music with mixes/beats/samples from Beatport are not recognized** as official releases — Lidarr can't automate them
- **Artists not in MusicBrainz** simply don't exist in Lidarr. Workaround: manually add via `lidarr:mbid` but that defeats the purpose
- **Release types determined by MusicBrainz** — if a release isn't categorized there, Lidarr can't find it
- Missing albums are a common complaint, especially for niche genres

### Lidarr vs Raw Soulseek for Underground Electronic

| | Lidarr + Soularr | Direct slskd (our approach) |
|---|---|---|
| **Catalog** | Only MusicBrainz entries | Anything any Soulseek user has |
| **Underground coverage** | Poor — many artists missing | Excellent — niche collectors share rare stuff |
| **Automation** | Good if artist is in MB | Need to build our own |
| **Matching** | Rigid (needs exact MB match) | Flexible (fuzzy matching, our rules) |
| **Format control** | Limited | Full (we choose FLAC/WAV/AIFF preferences) |

**Verdict**: Lidarr is great for mainstream music management but **wrong tool for underground electronic**. Direct slskd integration with our own matching logic is the right call. This is what SoulSync figured out too — they use slskd directly, not through Lidarr.

Source: [Lidarr FAQ](https://wiki.servarr.com/lidarr/faq), [Lidarr issues](https://github.com/Lidarr/Lidarr/issues)

---

## 3. SoulSync Deep Dive — Can We Use/Fork/Integrate?

### Architecture (100K lines, Python 3.11 + Flask + SQLite)

**Matching Engine** — the crown jewel:
- Generates 4–6 query variations per track (handles edge cases)
- Unicode/accent normalization (KoЯn, Björk, A$AP Rocky)
- Weighted confidence scoring across title, artist, duration
- Detects album variations (Deluxe, Remastered, etc.)
- **Source-specific weighting**: Soulseek prioritizes artist matching, YouTube emphasizes title
- Protects short titles ("Love" won't match "Loveless")
- Strictly rejects remixes when originals requested

**Download Orchestrator**:
- Routes between Soulseek (FLAC priority) and YouTube (fallback)
- Batch processing with concurrent workers + auto retry
- Source reuse: same uploader for full album consistency
- Quality presets: Audiophile / Balanced / Space Saver
- Format fallback chain: FLAC → MP3 → next best
- Per-format min/max file size rules
- Duplicate prevention against existing library

**Verification**: AcoustID fingerprinting to verify downloads are correct tracks. Fail-open design (errors don't block, only confident mismatches reject).

**Metadata Pipeline**:
- Dual-source: Spotify primary, iTunes/Apple Music fallback
- MusicBrainz enrichment with 90-day cache
- Synced lyrics from LRClib
- Album art embedding via Mutagen

**Discovery Engine**:
- Release Radar, Discovery Weekly, seasonal playlists
- 12+ personalized playlist types
- Sources: Spotify, Tidal, YouTube, Beatport charts

**Integrations**: Spotify, iTunes, Tidal, YouTube, Beatport (39 electronic genres), ListenBrainz, Soulseek/slskd, MusicBrainz, AcoustID, music-map.com, Plex, Jellyfin, Navidrome, LRClib

### Can We Use It?

**Option A: Run SoulSync alongside our app**
- Pro: Get all features immediately, battle-tested
- Con: 100K lines is massive, Python+Flask+SQLite+JS SPA, its own web UI, Docker deployment — it's a full separate application, not a library
- Overlap: We'd have two systems doing similar things

**Option B: Fork and strip down**
- Pro: Cherry-pick matching engine, download orchestrator, metadata pipeline
- Con: 100K lines means heavy coupling, hard to extract cleanly
- Risk: Maintenance burden of a fork

**Option C: Study and reimplement the good parts** ← RECOMMENDED
- Extract the **matching algorithm logic** (query generation, fuzzy matching, confidence scoring)
- Copy the **quality preset/format preference** approach
- Adopt the **AcoustID verification** pattern
- Use their **file organization template** concept
- Skip: Discovery engine (we have our own sources), web UI, media server integration

### Key Code to Study
- Matching engine: query generation, normalization, confidence scoring
- Download orchestrator: source routing, retry logic, concurrent workers
- Quality profiles: format preferences, size validation

Source: [SoulSync GitHub](https://github.com/Nezreka/SoulSync)

---

## 4. Tubifarry Deep Dive — Can We Use/Fork/Integrate?

### Architecture (Lidarr C# Plugin)

**What it does**: Extends Lidarr with extra indexers (Spotify, slskd, YouTube) and download clients.

**Key Components**:
- **Indexers**: Spotify catalog search, slskd/Soulseek search, YouTube search
- **Download Clients**: YouTube (via yt-dlp + FFmpeg), slskd
- **Codec Tinker**: Rule-based format conversion (`wav -> flac`, `AAC>=256k -> MP3:300k`, `all -> alac`)
- **MetaMix**: Multi-source metadata (Discogs, Deezer, Last.fm + MusicBrainz) with priority hierarchies
- **Lyrics Fetcher**: LRClib synced lyrics + Genius plain text
- **Search Sniper**: Randomized background searching to avoid indexer overload
- **Queue Cleaner**: Handles failed imports (rename from metadata, retry, blocklist)

**slskd Integration**: Queries slskd API as both indexer (search) and download client (retrieve).

**YouTube**: Extracts audio, converts with FFmpeg. Handles bot detection via cookie auth or trusted session generator (Node.js). Quality: 128kbps AAC standard, 256kbps with YouTube Premium.

### Can We Use It?

**No — wrong platform for us.** Tubifarry is a C# Lidarr plugin. We're Python. And we've established Lidarr isn't right for underground electronic.

**But steal these ideas:**
- **Codec Tinker rule syntax** — `wav -> flac`, `AAC>=256k -> MP3:300k` is an elegant conversion DSL
- **MetaMix multi-source metadata** with priority hierarchies — great pattern
- **Search Sniper** randomized background searching — useful for Soulseek to avoid hammering
- **Queue Cleaner** retry/blocklist logic for failed downloads

Source: [Tubifarry GitHub](https://github.com/TypNull/Tubifarry)

---

## 5. Beets — The Metadata Foundation

### What It Is
Python music library manager. Auto-tags via MusicBrainz, fetches art, lyrics, converts formats, manages your whole collection. Plugin ecosystem is massive.

### Relevant Plugins
- **chroma**: AcoustID fingerprinting for track identification
- **convert**: Format conversion (uses FFmpeg under the hood)
- **fetchart**: Album art from various sources
- **lyrics**: Fetch lyrics
- **lastgenre**: Genre tagging from Last.fm
- **mbsync**: Sync metadata updates from MusicBrainz
- **edit**: Interactive metadata editing
- **replaygain**: Loudness normalization

### Soulbeet — Beets + slskd Already Exists
[Soulbeet](https://github.com/terry90/soulbeet) bridges slskd and Beets:
- Search via MusicBrainz or Last.fm metadata
- Download from Soulseek
- Auto-import with `beet import` for tagging and organization
- Docker-compose setup with slskd + beets + soulbeet

### How We Should Use Beets
**Not as our primary app, but as a post-processing pipeline:**
1. Download tracks via our slskd integration
2. Run `beet import` on downloads for auto-tagging + organization
3. Use beets plugins for art, lyrics, genre tagging, format conversion
4. This replaces building our own metadata/conversion modules from scratch

**Alternative**: Use Mutagen directly for lightweight metadata ops, Beets for heavy lifting when full auto-tag is needed.

Source: [Beets](https://beets.io/), [Soulbeet](https://github.com/terry90/soulbeet)

---

## 6. Wishlist & Content Organization

### The Need
You consume niche electronic content from specific sources (Dr Banana, Velvet Velour, Alec Falconer, etc.) and need a central place to:
- Collect track/release wishlists from these sources
- Track what you've downloaded vs what's pending
- Feed wants into the Soulseek search pipeline
- Organize by source/curator/date

### Proposed: `wanted.json` System

```json
{
  "sources": {
    "dr-banana": {
      "url": "https://...",
      "type": "blog",
      "last_checked": "2026-02-26"
    },
    "velvet-velour": { ... },
    "alec-falconer": { ... }
  },
  "wants": [
    {
      "artist": "Unknown Artist",
      "title": "Track from Dr Banana mix at 23:15",
      "source": "dr-banana",
      "added": "2026-02-26",
      "status": "pending",
      "identified_via": null,
      "soulseek_status": null,
      "notes": "heard in Feb 2026 mix, deep house vibes"
    },
    {
      "artist": "Some Producer",
      "title": "Specific Track",
      "source": "velvet-velour",
      "added": "2026-02-20",
      "status": "downloaded",
      "identified_via": "acoustid",
      "file_path": "/path/to/file.flac"
    }
  ]
}
```

**Workflow**:
1. **Capture**: Add wants manually, from track ID results, from blog scrapes
2. **Identify**: Run unidentified wants through AcoustID → AudD pipeline
3. **Search**: Feed identified wants to slskd search
4. **Download**: Queue matches, verify with AcoustID
5. **Tag**: Run through Beets for metadata
6. **Convert**: FFmpeg to target format (AIFF for DJ use)

This replaces the flat `wanted.txt` with something structured and trackable.

---

## 7. Integration Architecture — How It All Fits

```
┌─────────────────────────────────────────────────────────┐
│                    CONTENT SOURCES                        │
│  Dr Banana │ Velvet Velour │ Alec Falconer │ TraxDB │ ... │
└──────────────────────┬──────────────────────────────────┘
                       │ scrape / manual add
                       ▼
              ┌─────────────────┐
              │   WANTED LIST    │  (wanted.json)
              │  artist/title    │
              │  source/status   │
              └────────┬────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌────────────┐ ┌──────────┐ ┌──────────┐
   │  IDENTIFY   │ │  SEARCH   │ │  MANUAL   │
   │ AcoustID    │ │  slskd    │ │  Add      │
   │ AudD (paid) │ │  YouTube  │ │           │
   └──────┬─────┘ └─────┬────┘ └─────┬────┘
          │              │            │
          └──────────────┼────────────┘
                         ▼
              ┌─────────────────┐
              │   DOWNLOAD       │  (slskd-api, yt-dlp)
              │  Quality prefs   │  FLAC > WAV > MP3
              │  Smart matching  │  (from SoulSync patterns)
              │  AcoustID verify │
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │   POST-PROCESS   │
              │  Beets auto-tag  │
              │  FFmpeg convert  │  → AIFF for DJ
              │  File organize   │  Artist/Album/Track
              │  Art + lyrics    │
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │   LIBRARY        │
              │  Organized files  │
              │  Metadata clean   │
              │  DJ-ready format  │
              └─────────────────┘
```

### What We Build vs What We Borrow

| Component | Build | Borrow From |
|-----------|-------|-------------|
| Content scraping (TraxDB, blogs) | ✅ Build | — |
| Wanted list management | ✅ Build | — |
| Track identification | ✅ Build | AcoustID (lib), AudD (API) |
| Soulseek matching | ✅ Build | SoulSync patterns, slskd-api |
| Download orchestration | ✅ Build | SoulSync patterns |
| Quality preferences | ✅ Build | SoulSync presets, Tubifarry rules |
| Metadata tagging | Borrow | Beets, Mutagen |
| Format conversion | Borrow | FFmpeg, Tubifarry DSL idea |
| File organization | ✅ Build | SoulSync template system |
| Classification (future) | ✅ Build | librosa, essentia |

---

## 8. Updated Roadmap

### Phase 1: Foundation
- [ ] Replace ShazamKit with AcoustID (free) + AudD (fallback)
- [ ] Implement `wanted.json` structured wishlist system
- [ ] Rewrite slskd matching (study SoulSync's matching engine)
- [ ] Fix TraxDB incremental scraping

### Phase 2: Post-Processing Pipeline
- [ ] Integrate Beets for auto-tagging downloaded tracks
- [ ] FFmpeg conversion module (Tubifarry DSL-inspired rules)
- [ ] File organization with templates

### Phase 3: Sources & Discovery
- [ ] Blog scrapers for niche sources (Dr Banana, Velvet Velour, etc.)
- [ ] TrackID.net-style mix tracklist extraction
- [ ] YouTube/SoundCloud mix → segmented identification

### Phase 4: Intelligence
- [ ] Music classification from spectrograms
- [ ] Learn from user's existing library organization
- [ ] Smart recommendations based on listening patterns

### Platform Decision
Keep Python for all logic. Swift UI optional thin layer over FastAPI backend. Or go full Python web UI. Decide after Phase 2.
