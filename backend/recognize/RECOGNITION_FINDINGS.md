# Recognition Engine Comparison — March 2026

## Test Mix
- **DSRPTV LIVE 039** — dsrptv soundsystem live @ ddd on Veneno
- SoundCloud: `dsrptvrec/dsrptv_live_039-dsrptv-soundsystem-live-ddd-on-veneno`
- Duration: 1h03m43s (3823s), 192kbps MP3
- Genre: Underground electronic / house / techno

## Scan Parameters
- Segment duration: 12s
- Step interval: 10s (382 segments total)
- Full mix scanned with all three engines independently

## Engine Results

### TrackID.net (ACRCloud 150M+ fingerprints)
- **6 unique tracks, 7 detections**
- Hit rate: N/A (server-side processing, not segment-based)
- All tracks verified correct — highest precision of all engines

| Time | Artist | Title | Label |
|------|--------|-------|-------|
| 10:45 | Fresh & Low | New Life | Re:Fresh Musik |
| 15:18 | Da Sunlounge | Chicago | Om Records |
| 23:50 | MADVILLA | Down 4 Me | Hot Wings |
| 25:41 | MADVILLA | Down 4 Me (cont.) | Hot Wings |
| 45:04 | Laurie Anderson | O Superman (Remastered) | Nonesuch |
| 55:06 | Rude Boy | Restless | Gross National Product |
| 56:58 | Fedo | Film Noir | Chippy Chasers |

### Shazam (shazamio)
- **41 unique tracks, 158 hits**
- Hit rate: 41.4%
- Strongest engine for multi-segment consistency
- Best at: well-known tracks, tracks with distinctive melodies

**Strong hits (3+ segments):**

| First Hit | Artist | Title | Segments |
|-----------|--------|-------|----------|
| 1:20 | X-District | Color Correction | 13 |
| 7:10 | Yes | Owner of a Lonely Heart | 3 |
| 9:20 | Fresh & Low | New Life | 34 |
| 38:10 | Sweely | Around | 21 |
| 46:50 | The Prince Karma | Later Bitches (remix) | 10 |
| 49:30 | Oliver Dollar & Jimi Jules | Pushing On (Tchami Remix) | 5 |
| 56:40 | Fedo | Film Noir | 38 |

### ACRCloud (our direct API, same 150M DB)
- **39 unique tracks, 49 hits**
- Hit rate: 12.8%
- Mostly single-segment, low-score noise (25-37)
- False positive hotspot: 2230-2370s region (14 production music tracks)

**Only reliable hit:**

| First Hit | Artist | Title | Segments | Avg Score |
|-----------|--------|-------|----------|-----------|
| 26:00 | MADVILLA | Down 4 Me (Original Mix) | 2 | 25 |
| 41:40 | Anatoly Space | Escape from Reality | 6 | 30 |

## Cross-Engine Comparison

### TrackID tracks found by our engines

| Track | TrackID | Shazam | ACRCloud |
|-------|---------|--------|----------|
| Fresh & Low - New Life | Yes | **Yes (34 segs)** | No |
| Da Sunlounge - Chicago | Yes | No | No |
| MADVILLA - Down 4 Me | Yes | No | Weak (2 segs, score 25) |
| Laurie Anderson - O Superman | Yes | No | No |
| Rude Boy - Restless | Yes | No | No |
| Fedo - Film Noir | Yes | **Yes (38 segs)** | No |

**Result: 2/6 TrackID tracks found by at least one engine. 4/6 missed by both.**

### Tracks only our engines found (not on TrackID)

| Track | Engine | Segments |
|-------|--------|----------|
| X-District - Color Correction | Shazam | 13 |
| Yes - Owner of a Lonely Heart | Shazam | 3 |
| Sweely - Around | Shazam | 21 |
| The Prince Karma - Later Bitches | Shazam | 10 |
| Oliver Dollar & Jimi Jules - Pushing On | Shazam | 5 |

## Key Findings

1. **Shazam is our strongest segment-based engine** — 41.4% hit rate vs ACRCloud's 12.8%
2. **ACRCloud direct API returns mostly noise** — nearly all hits are single-segment with scores 25-37 (false positives from production music libraries)
3. **TrackID.net uses ACRCloud's full processing pipeline** (longer analysis windows, better filtering) which explains why it finds tracks our direct ACRCloud API calls miss
4. **Zero overlap between ACRCloud and Shazam** on this mix — they cover completely different catalogs
5. **The 3-engine merge is the right strategy** — each engine finds tracks the others miss

## Targeted Testing Results (6 durations x 3s step x 2 engines per target)

### MADVILLA - Down 4 Me (23:50-27:28) — SOLVED via multi-segment ACR acceptance
- ACRCloud: **31 hits** across all segment durations (8s-30s), score 25-34
- Shazam: 0 direct matches
- **Fix:** Accept ACRCloud score 25+ when 3+ segments match (clustering.py)

### Laurie Anderson - O Superman (45:04-45:46) — SOLVED via title similarity grouping
- ACRCloud: 1 hit as "Age Of Luv - O Superman" (score 25)
- Shazam: **6 hits** as remixes — "Marcello Giordani - O Superman (Disco Spacer Mix)",
  "Mandy & Booka Shade - O Superman" — all share the iconic vocal sample
- **Fix:** Title similarity grouping merges all "O Superman" variants (clustering.py)

### Da Sunlounge - Chicago (15:18-18:44) — UNFIXABLE (DB gap)
- 534 segment attempts across both engines, 6 durations, 3s step = zero matches
- Not in either fingerprint database. Only TrackID's full-stream processing finds it.

### Rude Boy - Restless (55:06-56:21) — UNFIXABLE (DB gap)
- 270 segment attempts across both engines = zero matches
- Too underground (Gross National Product label). Not fingerprinted anywhere.

## Algorithm Improvements Implemented

### 1. Title similarity grouping (`clustering.py:_group_by_title_similarity`)
- Merges covers/remixes/samples that share the same core title
- Extracts core title by stripping parenthetical content: "O Superman (Disco Spacer Mix)" → "o superman"
- Only merges when core title is 2+ words (avoids generic titles like "love")
- Requires 2+ total segments across the group

### 2. ACRCloud noise filtering (`clustering.py:_resolve_conflicts`)
- Drops single-hit ACR results with score < 40 (overwhelmingly false positives)
- Keeps multi-segment low-score hits (3+ segments) — these are real signal
- Tested: 2230-2370s region had 14 false positive production music tracks, all single-hit

### Coverage after improvements: 4/6 TrackID tracks (up from 2/6)

## Technical Notes

### TrackID.net Cloudflare
- As of March 2026, Cloudflare Turnstile blocks all server-side requests
- `cloudscraper`, `curl_cffi`, headless Playwright all fail
- Only visible (non-headless) Playwright passes the challenge
- Production solution: store `cf_clearance` cookie from browser, or use FlareSolverr

### TrackID URL Mismatch
- SoundCloud URLs often differ between original and TrackID's indexed version
- Added keyword search fallback to `lookup_by_url()` to handle this

### ACRCloud Score Interpretation
- Scores 25-40: single-hit = noise, multi-segment (3+) = real signal
- Pipeline now filters single-hit ACR < 40, keeps multi-segment low-score hits
- Multi-segment confirmation is the key reliability signal for ACRCloud
