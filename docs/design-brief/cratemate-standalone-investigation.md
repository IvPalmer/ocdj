# Cratemate Standalone — Recognition Feasibility Investigation

**Date:** 2026-06-28 / 2026-06-29
**Goal:** Scope extracting Cratemate from ocdj into a standalone **iOS** product to sell, and
find the **cheapest sustainable recognition pipeline** (ideally no flagship LLM / "no agents").
**Status:** Investigation complete. Recommendation below. No build started.

---

## 1. How Cratemate works today (in ocdj)

Album-cover identifier + metadata enricher. Photo of a sleeve → artist/album/label →
Discogs/Spotify/YouTube/Bandcamp enrichment.

Pipeline (`backend/cratemate/services/hybrid_search.py`):
- **Pass 1 — Claude Opus vision** (`claude_vision.py`): reads the sleeve, returns
  artist/album/label/confidence. Prompted "artist = null unless printed or globally iconic"
  to suppress hallucination. Auth via `CLAUDE_CODE_OAUTH_TOKEN` (Max subscription, $0/call).
- **Pass 2 — catalog match**: if only a label is found, Opus picks from a Discogs label-catalog list.
- **Pass 3 — perceptual-hash verify** (pHash/dHash): rejects Discogs candidates whose artwork is too far from the photo.

Stack: Django 5.2 + DRF, 3 small Postgres tables (audit log only — core ID is stateless),
runs synchronously in-request (no worker needed). React component `CratematePanel.jsx`.
Coupling to ocdj is low; the `services/` layer is self-contained (~2000 LOC to lift).

**Key for iOS:** you cannot bundle Python/Django on iOS. A standalone iOS app = native Swift
client → hosted backend (reuse the Django `services/`). The React frontend is thrown away.

---

## 2. The core question: can we go cheap / no-LLM?

Tested empirically (not theorized). All tests used real records; vision tests used the **actual
`ClaudeVisionCollector` pipeline** at full resolution.

### Test A — model tier on 5 covers (Opus vs Sonnet vs Haiku)

Covers turned out to be Giegling (×3), Warp (Smokers' Delight), Drop Music (Inland Knights comp).
Verified all picks are real Discogs releases (not hallucinations).

| Cover | Opus | Sonnet | Haiku |
|---|---|---|---|
| Smokers' Delight (Warp) | ✅ +label | ✅ | ⚠️ misspelled→miss |
| Kettenkarussell – Insecurity Guard (Giegling) | ✅ | ⚠️ label hallucinated "Riesling" | ❌ null |
| Leafar Legov – Family (Giegling) | ✅ | ⚠️ wrong album "Panic" | ❌ null |
| Various – Südstadt (Giegling) | ⚠️ album+label, artist null | ⚠️ label hallucinated | ❌ wrong |
| Inland Knights comp (Drop) | ✅ | ✅ | ⚠️ misread label |
| **Clean Discogs resolve** | **4/5** | ~2/5 | ~0/5 |

**Finding:** "just use a cheaper model" is **empirically false**. Haiku is unusable; Sonnet
phonetically corrupts labels (Giegling→Riesling/Riegling) and misses titles. Opus carries it.
Quality today **is** an Opus dependency.

### Test B — barcode prevalence (Discogs API, the user's niche)

| Cohort | Barcode present | Catalog # present |
|---|---|---|
| Underground techno 12" | ~5–6/12 (~50%) | **12/12 (100%)** |
| Generic electronic vinyl | 0/12 | (always) |

**Finding:** barcodes are unreliable for the niche (post-1990 mainstream feature). **Catalog
numbers are near-universal** and Discogs supports exact `catno=` lookup (GIEGLING 18 → DJ Metatron ✅,
NonPlus 12 → Boddika ✅). Catch: format variance (PERLON 99 vs PERL 99) needs normalization.

### Test C — sealed-record reality (front/back only, no runout access)

Pulled Discogs's own front + "back" (often the disc label) images for obscure 12"s.
Most obscure 12"s ship in **generic sleeves** → what you see sealed is the disc label through the
die-cut, or a printed picture sleeve. Of records with a visible printed surface, the catalog
number / artist / title is usually present → OCR-able. White-label promos show only a label name
(or a shop sticker, which is NOT the release barcode) → unidentifiable sealed.

### Test D — the user's actual Discogs wantlist (6 random, sealed-store view)

5/6 had readable catno and/or artist/title on the visible surface → **OCR → Discogs**. Zero
needed Opus. The 1 failure was an unofficial Macy Gray white-label bootleg (only a shop price
sticker) — unsolvable sealed for anyone.

### Test F — full scenario matrix on 87 wantlist covers (Apple Vision OCR, clean scans)

Ran the real on-device engine (Apple Vision `VNRecognizeTextRequest` + `VNDetectBarcodes`,
compiled Swift) over all 87 wantlist releases (fronts + 101 back/label images), scored vs
ground-truth catno/artist/title with a fuzzy resolver. **Clean Discogs scans = best case ceiling.**

| Scenario | Resolvable |
|---|---|
| Front OCR only | **52%** (45/87) |
| Back/label OCR only | 39% (secondaries are mostly disc labels — worse) |
| Combined front+back OCR | **59%** (52/87) |
| Barcode detected | **3/87 (~3%)** — useless for the niche |
| + Opus on the 35 OCR-misses | **+20%** (18 rescued) |
| **= OCR + Opus total** | **80%** (70/87) |
| **Unidentified by everything** | **19%** (17/87) |

Findings:
- Free OCR alone ≈ **59% ceiling** (clean). Real shop photos will be lower (front-only already 52% clean).
- **"Shoot the back" does NOT rescue it** — disc-label secondaries OCR'd worse (39%); many niche 12"s
  have no printed back sleeve.
- **Opus is a +20% workhorse, not a rare fallback** — rescues exactly OCR's blind spots (every
  Giegling, Phone Traxxx, the comps, the Snoop bootleg). OCR + Opus are complementary.
- **~1 in 5 records unidentifiable by anything** (textless/obscure Opus never memorized) → manual only.
- Data **flatters Opus**: wantlist is Giegling-heavy, exactly what Opus memorized. Other collections = less rescue.

Implications: OCR can't be the sole engine AND Opus can't be "occasional" — both core → more Opus
calls → subscription economics mandatory; "couldn't identify → manual" must be a first-class ~20% path.

### Test E — genuinely textless covers (the hard tail)

Ran real Opus Pass-1 on no-text art covers from two famous deep-house labels:
- **Giegling** (Dwig, Matthias Reiling) — Opus ✅ every one (memorized the catalog).
- **Smallville** (Christopher Rau – The Keys, Stefan Marx textless art) — Opus ❌ **blank/null**,
  despite Smallville being equally famous. (An apparent Smallville hit was Opus *reading circular
  handwritten text* on the label, not recognizing art.)

**Finding:** on textless covers, Opus is a **label lottery** — it works only for catalogs it
memorized, and you cannot predict which. When it's blind: no text to OCR + art not memorized +
no database = **unidentifiable by any cheap method**.

---

## 3. The complete map (what tool wins, by record type)

| Cover type | Best tool | Cost | Reliable? |
|---|---|---|---|
| Has artist/title/catno text (the majority) | **OCR → Discogs** | ~free | ✅ yes |
| Textless, Opus-memorized label (e.g. Giegling) | **Opus** | paid | ⚠️ only if it knows the label |
| Textless, unknown label (e.g. Smallville) | **none cheap** | — | ❌ needs image-index or runout |
| Blank white-label / bootleg | **none** (runout only) | — | ❌ unsolvable sealed |

---

## 4. Approaches considered and why ruled in/out

- **Cheaper vision LLM (Haiku/Sonnet)** — ❌ ruled out as primary. Measurably worse (Test A).
- **Reverse image search live against Discogs** — ❌ impossible. Discogs has no image-search API,
  ~60 req/min limit.
- **Google Vision Web Detection** (~$3.50/1k, Lens-grade) — ⚠️ returns "similar images / pages",
  not a Discogs ID; fails on abstract textless covers (exactly where needed). Resolver needed.
  Could not run live (no GCP key in `.env`).
- **Self-hosted CLIP/embedding index over Discogs covers** — ⚠️ the only thing that could beat
  Opus on the long tail, but a large build:
  - Store **embeddings not images** (~9GB for all 17M releases — storage is trivial).
  - Real blockers: (a) **ingestion** — fetching 17M images once to embed at 60/min ≈ 196 days
    single-token; token-sharding = ToS circumvention; legit routes = Discogs partnership /
    commercial data access; (b) **legal** — embeddings are derived from copyrighted art;
    "not images = safe" is NOT established (needs counsel, allowlists, takedown); (c) **long tail**
    — lazy "cache on miss" does NOT help obscure records (sparse repeat scans).
  - Discogs dumps are CC0 but **metadata only** (no image URLs). Cover Art Archive is bulk but
    skews mainstream.
  - **Verdict:** the endgame, not the start. Don't build speculatively. (Record Scanner, the iOS
    competitor, almost certainly built exactly this — proves it's a viable business but a big lift.)

---

## 4b. Cover-art index feasibility (after Record Scanner evidence) — codex VERDICT: BUILDABLE

Record Scanner was tested on the exact covers our OCR+Opus pipeline FAILED (textless/abstract) and
identified several **instantly** → it runs a precomputed **cover-art embedding/fingerprint index**
(ANN nearest-neighbor), not OCR/LLM. That index beats us on the textless tail. So the index — earlier
called "the endgame" — is the competitor's working moat.

**Is it buildable? Yes (codex-confirmed).** But "download all 17M Discogs images and store them linked
to releases" is the wrong framing:
1. **Store embeddings (~512-dim, ~9GB total) + release_id, NOT images** — storing images = copyright
   redistribution problem + size.
2. **Don't need all 17M — scope to the niche** (electronic/house/techno ~1-3M), defined by the user's
   actual wantlist/collection genre distribution. Beats our OCR pipeline where it matters; won't match
   Record Scanner globally.
3. **Bottleneck isn't storage — it's acquisition.** Operationally the ~60 req/min API limit (full
   catalog ≈ 196 days single-token; token-sharding = ToS violation). Legally, Discogs terms prohibit
   bulk harvest / derivative image DB — **cannot build on unsanctioned scraping.**

**The two real risks (not the database):** (a) legal/data access — acquiring covers legitimately at
scale; (b) phone-photo robustness — generic CLIP/SigLIP isn't robust to sealed-sleeve photos
(perspective/glare/shrink) without fine-tuning.

**Smarter acquisition (hybrid):** user wantlists/collections + scan-demand + Cover Art Archive +
label/retailer feeds (Bandcamp/Beatport/Juno/HHV/Hard Wax) + on-demand backfill from misses + user
corrections growing a private index. ANN proposes, OCR/catno disambiguates.

**Effort (solo):** weeks → proof (5k-50k covers, ANN, rectify, real-photo eval); 2-4 months → useful
niche-index product; full-catalog parity not solo-feasible without a data deal.

**De-risk first:** retrieval benchmark — few thousand niche covers + REAL phone photos → top-1/5/20
recall with CLIP/SigLIP/DINOv2 + perspective correction + OCR rerank. Strong top-20 → green-light;
weak → stop or budget for fine-tuning. Do this before any infrastructure.

## 4c. Retrieval benchmark — first run (Apple Vision feature-print, on-device)

Built a real retrieval index using **Apple Vision `VNGenerateImageFeaturePrint`** (768-d, L2-normalized,
zero-install, on-device iOS parity). Reference index = 87 wantlist primaries + 841 niche decoys (928
covers). Queries = 101 alternate Discogs scans of the wantlist releases (Tier-B "scan-to-scan").

| Metric | Result |
|---|---|
| top-1 recall | 54% |
| top-5 recall | 61% |
| top-10 recall | 65% |
| MRR | 0.58 |

Confidence/trust (cosine of top-1 hit): accept ≥0.80 → 40% accepted @ **90% precision**; accept ≥0.85
→ 32% accepted @ **100% precision**. Clean separation (correct hits mean sim 0.87 vs wrong 0.69).

**Reading:** queries here are mostly **disc-label/back scans (cross-content vs the front reference)** — a
brutally conservative test; real front→front (or phone-front) should score higher. Even so, 54% top-1
on the FREE on-device engine with clean high-confidence precision is a **promising floor**. Caveats:
only 841 decoys (2k+ wanted; more lowers recall slightly), Apple feature-print is the baseline
(CLIP/DINOv2 likely better), and the verdict-grade test is still **real phone photos** (Tier C).

Tooling left in scratchpad: `fptool` (Swift feature-print), `benchmark.py`, `query_phone.py`
(drop sleeve photos in `queries_phone/` → top-5). Decoys + covers downloaded locally.

## 4d. Phone-photo retrieval test (VERDICT-GRADE) — image retrieval WORKS

Tested 5 REAL iPhone photos of physical sleeves against the on-device Apple Vision feature-print index.
First run: all 5 scored low — because **none were in the index** (they were owned records, not wantlist).
This correctly produced **zero false-accepts on real photos** (good trust signal). Then we found 4 of the
records on Discogs, added their covers to the index, and re-ran:

| Phone photo | Result | top-1 sim | best decoy |
|---|---|---|---|
| Marschmellows – Flash Fried | top-1, **auto-match** | **0.896** | 0.676 |
| Numa Gama – A Spectral Turn | top-1, **auto-match** | **0.822** | 0.673 |
| Nick Luscombe – Tokyo Dreaming | top-1 (confirm) | 0.731 | 0.585 |
| Magic Touch – Just Wanna Feel | top-1 (confirm) | 0.576 | 0.559 |
| striped cover (not in index) | correctly rejected | 0.455 | — |

**4/4 in-index records retrieved at rank #1 from a real phone photo** (vs 841 decoys, free on-device
engine) + correct rejection of the out-of-index one. This **reverses the OCR pessimism** — image
retrieval is the stronger engine (OCR ceiling was 52-59%; retrieval is top-1 on real photos).

Findings: high-contrast covers auto-match (≥0.80); busier ones rank #1 but lower → **confirm-among-
candidates UX essential**, and **relative margin (top1 vs top2) beats a fixed threshold** (Just Wanna
Feel: 0.576 correct vs 0.559 decoy — thin). Caveats: n=4 (need more photos for a real %); photos were
fairly clean (angle/glare will lower sims → **sleeve auto-crop is the next lever**); only 841 decoys.

**Conclusion: the covers DB + on-device image retrieval is THE engine; OCR/Opus become secondary
(catno disambiguation, textless edge cases). Building the covers DB is validated.**

## 4e. Coverage test — general vs demand-weighted seed (DECISIVE)

Built a real 15,062-cover multi-genre seed library (legit Discogs search harvest, embeddings via Apple
Vision feature-print) and tested two real wantlists against it WITHOUT pre-adding their records
(queries only; library-only index — no self-match leakage).

**General random seed (15k) coverage of real collections:**
| Collection | Exact release-id | Artwork retrieval |
|---|---|---|
| rapha.palmer (87 wants) | **0/87 (0%)** | 0/87 (0%) |
| PankoVinyl (8,661 wants) | **26/8661 (0.3%)** | ~66 (0.8%) |

→ **A general random seed is useless for niche collections.** 15k is 0.08% of Discogs scattered across
12 genres; expected overlap with specific obscure records ≈ 0. Stays near-zero even at 10× size.

**Demand-weighted seed (harvest the 57 labels rapha collects → 6,141 covers):**
| rapha.palmer | Exact release-id (DB completeness) | "Artwork retrieval" |
|---|---|---|
| General seed only | 0/87 (0%) | 0% |
| **+ label-targeted seed** | **76/87 (87%)** | 75/87 (86%) — see correction |

⚠️ **CORRECTION (adversarial verification, 4 agents + codex):** the "86% artwork retrieval" is
**MISLEADING — it measures self-match, not recognition.** The label-seed harvests each wanted release
*from its own label's catalog*, so the query cover is *in* the seed; **64/87 query covers are
byte-identical (md5) to a seed cover** → the query matched a copy of itself. The "86% artwork" and "87%
exact-id" are ONE measurement (DB completeness), not two confirmations. PankoVinyl confirms: threshold
sweep flattens 66→27→26→26 (self-match signature, not a recognition curve).

**The two problems were conflated — separate them:**
- **Completeness** (records present in DB): label-targeting → **~87%**. Real & useful — demand-weighted
  seeding efficiently *populates* the DB. NOT recognition.
- **Recognition** (new photo → DB match): the legit held-out test (`benchmark.py`, alternate scans vs
  primaries+decoys) = **54% top-1, ~39% after removing duplicate queries**; 840 decoys is *easier* than
  a real index so likely lower at scale. Phone photos 4/4 but n=4. **This is the real engine number:
  ~40–54%, not 86%.**
- **False-accept**: 0/35 out-of-index at 21k (max 0.796 < 0.85) — controlled, but smoke test only.

**Scaling check — PankoVinyl (8,661 wants, 2,495 labels):** general seed 26/8661 (0.3%) → + top-200
label-seed (36,212 covers) = **3,251/8,661 (37.5%) completeness**. Lower than rapha's 87% because (a)
top-200 labels = only 57% of his wants (huge label spread), (b) 3-page/label cap misses most of huge
classic-house catalogs (Strictly Rhythm/UMM/Nu Groove have 1000s each; rapha's boutiques have ~50).
**Completeness = f(harvest depth × label breadth)** — a dial, not a wall; deeper harvest → higher.
(Still completeness, not recognition: exact-id 37.5% ≈ artwork 37% = self-match.)

**Conclusion: acquisition must be DEMAND-WEIGHTED (solves completeness — validated at both 87-record and
9k scale). But RECOGNITION
with the baseline on-device model + no cropping is only ~40–54% and needs work** (sleeve crop —
Vision's rect detector failed on white-on-white; stronger embedding model CLIP/DINOv2; held-out
validation) before it's a "robust Record Scanner." High-confidence matches are trustworthy
(false-accepts controlled), so confirm-or-manual UX is viable.

## 4h. TWO-STAGE recognition (global → geometric verification) — the algorithm fix (validated)

Problem: naive single global embedding (Apple Vision 768-d + flat cosine) DEGRADES at scale — a
low-info cover's style-twin decoy beats it as the decoy pool grows (Just Wanna Feel fell rank-1→#29→#246
as index went 841→99k). This is the textbook rising-false-positive ceiling of global descriptors.

Codex Rx (VERDICT): **two-stage — global copy-detection embedding for top-k candidates → LOCAL-FEATURE
geometric verification (SIFT/ORB or SuperPoint+LightGlue, RANSAC inliers) → OCR/catno fusion.** A
bigger DB then HELPS (verification rejects look-alikes), so no scoping/partitioning needed (store-
discovery use case = global recognition, not "search my collection").

Empirical test (real phone photos vs 99k index + SIFT verification, OpenCV):
| Photo | correct cover in DB? | SIFT inliers → verdict |
|---|---|---|
| Flash Fried | yes | **85** → confident match ✅ |
| Tokyo Dreaming | yes | **163** → confident match ✅ |
| A Spectral Turn | yes | **222** → confident match ✅ |
| Just Wanna Feel | NO (wrong release fetched — "JヤSト" photo vs "ジュスト" DB art, different pressing, same style) | **~0** → correctly "not found" ✅ |

**Geometric verification works: dozens–hundreds of inliers for true matches, ~0 for look-alikes** — a
hard, scale-invariant confidence signal that flat cosine lacks. The earlier "recognition degrades at
scale" applies only to the naive method; the two-stage pipeline is robust at 99k (3/3 confident correct
+ correct rejection of the not-in-DB case). The Just Wanna Feel "failure" was a GROUND-TRUTH error
(wrong Discogs release), which the algorithm handled correctly by refusing to match.

**Recognition pipeline to build:** crop/rectify sleeve → copy-detection embedding (SSCD/DISC-style, not
Apple/CLIP/DINO which are semantic) → global top-k (server) → SIFT/LightGlue RANSAC verification + OCR/
catno/barcode soft fusion → accept on strong inliers+margin, else top-few or "no confident match".
Thin phone (crop+query), server does candidate search + verification. DB growth HELPS. This is
almost certainly Record-Scanner/Google-Lens-class architecture (multimodal: visual + local verify + OCR).

## 4g. CAA/MusicBrainz coverage of the niche (CAA is NOT the answer for DJs)

Tested MusicBrainz/Cover Art Archive coverage of a 150-record sample of PankoVinyl's house/techno
wantlist: **30% in MusicBrainz metadata, but only 9% have CAA cover art.** Even when MB has the release,
the cover usually wasn't uploaded to CAA. **CAA bulk would add only ~9% for a DJ collection** —
mainstream-skewed, as predicted. (Strict artist+title match → possibly slight undercount; ~18% even
doubled. Still small.)

**Implication:** the underground catalog lives on **Discogs — no free bulk substitute.** "Go as far as
possible without a partnership" = **aggressive continuous Discogs label-pack harvesting** (electronic
scenes, deep) + demand-fetch, accumulating embeddings over time (rate-bounded). The **Discogs
Preferred-API-Partner agreement is the only true bulk unlock** for the niche. CAA optional (mainstream
only). Embeddings are cheap to store (~14GB whole catalog) — the constraint is acquisition RATE, not
storage; the DB grows monotonically toward comprehensive.

## 4f. Embedding model comparison — Apple vs CLIP vs DINOv2 (the model is NOT the lever)

Tested 3 embedding models for recognition (venv torch+MPS):
- **Scan-to-scan** (101 mixed alt-image queries vs 87 primaries + 841 decoys): Apple 54% / CLIP-L14 56% /
  **DINOv2-B14 58%** top-1. Marginal — a stronger model gains only +4%.
- **Real phone photos** (n=4, cropped, vs 4 covers + 841 decoys): Apple **4/4**, CLIP **4/4**, DINOv2 **3/4**.

**Conclusions:**
- Swapping to CLIP/DINOv2 does NOT meaningfully improve recognition; all plateau ~same.
- **Apple Vision feature-print is the best practical engine** — ties/beats the others on real photos,
  FREE, ON-DEVICE, reads HEIC natively (CLIP/DINO needed HEIC→JPG + a server). Keep it.
- Recognition on realistic front-sleeve photos is encouraging (4/4, glare/angle), better than the mixed
  54-58% proxy implies (proxy is dragged by back-cover/disc-label queries).
- The real recognition levers (since model isn't): **capture/crop UX** (modest sim boost, 0.73→0.80),
  **OCR + catalog# fusion** for hard textless/low-contrast cases, fine-tuning (heavy, needs beta data).
- Caveat: n=4 phone photos is directional; true recall % needs launch-beta data.

## 5. Recommendation — layered best-effort MVP

There is **no single engine that catches everything** (proven from every angle). Ship a layered
pipeline:

1. **OCR engine (free, on-device iOS Vision):** read catalog#, artist, title, label, barcode →
   Discogs `catno`/barcode/text lookup. Carries the text-bearing majority at $0.
   - Needs: **catno normalization** + **fuzzy Discogs resolver** (strip promo strings, handle
     spelling/format variance — current resolver is too brittle; even correct vision missed on
     long titles / misspellings).
2. **Opus fallback (paid):** for textless covers — rescues an unpredictable slice (memorized
   labels). Not the engine; a safety net.
3. **Honest UX:** **confirm-among-candidates** with confidence bands; for the rest, "couldn't
   identify — search manually / check the runout." Never a confident wrong guess. (False-confidence
   is the real enemy — mislabeling a same-art different pressing is worse than asking.)

### Product / cost model (from earlier discussion)
- iOS ⇒ native Swift client + hosted backend (reuse Django `services/`).
- Per-scan Opus cost ⇒ **subscription or freemium-credits**, NOT one-time. (Or BYO-key power tier.)
- iOS IAP ⇒ Apple takes 15–30%, StoreKit required.
- Competitor **Record Scanner** already does cover→Discogs cheap/free. Wedge = DJ-specific
  (electronic 12" focus, catno help, batch crate intake, honest confidence, the existing
  Discogs/Spotify/YouTube enrichment Cratemate already nails).

### The one test still unrun
OCR reading **small catalog numbers off real angled-through-shrinkwrap store photos** (glare,
perspective). Presence is proven (5/6 wantlist); real-world legibility is the open question.
Needs actual store-condition phone photos.

---

## 6. Operational notes / gotchas discovered
- `.env` `CLAUDE_CODE_OAUTH_TOKEN` had **expired** (401). Re-minted via `claude setup-token`
  (long-lived, 1yr) and written to `.env`. The macOS keychain/on-disk creds were also stale —
  subprocess `claude -p` 401s even when the GUI session works (GUI holds a refreshed in-memory token).
- `.env` `DISCOGS_USERNAME=ivpalmer` is **stale** — the personal token belongs to **rapha.palmer**
  (id 20826721), wantlist 87 items, collection 79. (Not changed — flagging only.)
- Chrome MCP `file_upload` sandbox blocks arbitrary local files (couldn't drive Google Lens with
  local covers). GCP Vision Web Detection needs a key not in `.env` — live RIS test still unrun.
