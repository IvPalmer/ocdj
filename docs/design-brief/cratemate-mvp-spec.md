# Cratemate iOS — MVP Spec & Best-Scenario Build (v2: image-retrieval engine)

**Date:** 2026-06-30
**Basis:** `cratemate-standalone-investigation.md` (all conclusions empirically tested, incl. the
phone-photo retrieval test §4d and acquisition reality §4b/§4e).
**One-liner:** iOS app that IDs a record from a sleeve photo via **on-device cover-art image
retrieval** (primary), with OCR/barcode/catalog# as narrowing+disambiguation signals and Opus as a
rare textless fallback; honest confirm-or-manual UX; freemium subscription.

> **v1→v2 change:** v1 made OCR the engine. The phone-photo test reversed that — image embeddings
> retrieved real photos at top-1 (OCR caps ~59%). **Image retrieval is now the engine; OCR is a
> narrowing/disambiguation signal.**

> **STATUS (2026-06-30, codex overall review): GO WITH CAVEATS.** Validated as a plausible recognition
> component with a sound, cheap architecture — NOT yet validated as a "better Record Scanner." Proceed
> as a closed TestFlight MVP to validate real-world recall; do not ship a polished paid standalone or
> claim parity yet. **Engine decided: Apple Vision feature-print** (free/on-device/HEIC-native; CLIP &
> DINOv2 tested, no meaningful gain — dropped). **#1 unvalidated risk: real-world recall** (only n=4
> real photos; the 54–58% mixed proxy is a warning). **Differentiation is weak unless narrowed to
> serious DJs/collectors.** Biggest design fix: **import-first onboarding** to beat cold-start.

---

## 1. Product shape
- **Native iOS app (SwiftUI).** Camera at a sleeve (sealed/in-shop): front, label, spine, barcode.
- **Hosted backend** (reuse ocdj `backend/cratemate/services/` for Discogs/Spotify/YouTube enrichment
  + the embedding index). Client holds no secrets.
- **Target user:** DJs / crate-diggers; electronic/house/techno first, general later.
- **Positioning:** "sealed-sleeve resolver, confidence-first" — NOT "scan any cover" (Record Scanner owns that phrase). Differentiate on DJ workflow + honest confidence + deep enrichment.

---

## 2. Architecture (image-retrieval primary)

```
iOS app
 ├─ Camera → detect + perspective-correct the square sleeve (crop out background)   ← robustness lever
 ├─ ON-DEVICE embedding: Apple Vision VNGenerateImageFeaturePrint (768-d, free, proven top-1 on real photos)
 │     └─ send the 512–768-d vector (NOT the image) to backend
 ├─ Tier 1  VISUAL RETRIEVAL: ANN nearest-neighbour over the cover-embedding index → top-k candidates
 │     • accept by RELATIVE MARGIN (top1 vs top2), not a fixed threshold (proven: 0.576 correct vs 0.559 decoy)
 ├─ Tier 2  NARROW/DISAMBIGUATE: on-device OCR (catno/artist/title) + barcode → filter/re-rank candidates,
 │     and resolve MBID→Discogs release (pick exact pressing among same-artwork variants)
 ├─ Tier 3  FALLBACK: index miss → Opus vision (paid) OR pure OCR→Discogs text search
 └─ Result as DECISION STATE: "Exact match" / "Pick among N" / "Weak guess" / "Couldn't identify (manual)"
```

**Why this shape (measured):** real phone photos → on-device feature-print → 4/4 top-1 vs 841 decoys,
zero false-accepts on out-of-index records. OCR alone capped at 59%. So retrieval leads; OCR narrows.

---

## 3. The cover index — acquisition (the hard part; see investigation §4b/§4e)

**"Download the whole Discogs catalog" is NOT achievable legitimately:**
- Discogs API 60/min → full catalog ≈ 208 days/IP; bulk harvest violates API ToS; dumps are
  metadata-only (no image URLs). No Discogs image bulk source exists.
- Cover Art Archive (only legit bulk source) maps to **only ~7–10% of Discogs** (MB→Discogs links =
  1.85M; CAA vinyl ≈ 340k) and **skews mainstream/digital** — the opposite of the vinyl long tail.
- **Full Discogs parity ⇒ commercial Discogs data agreement.** That's the only path.

**So the index is a legally-clean SEED + GROWTH LOOP, not a bulk download:**
1. **Seed:** bulk CAA (Internet Archive) → embeddings (~10% general coverage, free, no rate limit).
2. **MBID-first:** recognize against the MusicBrainz/CAA index; **resolve to Discogs AFTER retrieval**
   via probabilistic metadata match (barcode/artist/title/label/catno from OCR).
3. **Metadata:** Discogs CC0 dumps for release IDs/catno/label/title (complete, even without image).
4. **Demand-driven fill:** for misses (the obscure niche), fetch covers via Discogs API rate-limited,
   triggered by scans/wantlists/collections — NOT enumeration. (Proven: user's records, once added,
   hit top-1.)
5. **Grow:** user scans + corrections build a private high-value index over time.
6. **Parity later:** commercial Discogs agreement if full general coverage becomes required.

**Storage:** embeddings + release_id ONLY (not images) — ~9GB for the whole catalog if ever reached;
also the defensible posture (no redistribution/display). Cover-art copyright stays with labels/artists;
get counsel before commercial launch. (Bulk-downloading CAA is cleaner on *access mechanics*, not
copyright.)

---

## 4. Tiering, cost, monetization
| Tier | Path | Cost | Role |
|---|---|---|---|
| 1 | on-device embed → ANN visual retrieval | ~$0 | **engine** (top-1 on real photos) |
| 2 | OCR/barcode/catno narrow + Discogs resolve | ~$0 | disambiguate pressing, confirm |
| 3 | Opus fallback (index miss / textless-unknown) | paid | rare safety net |
| — | "couldn't identify → manual" | $0 | the genuine unsolvable tail |

- **Monetization:** freemium → **subscription** via iOS StoreKit (Apple 15–30%). BYO-key is dead on
  App Store (guideline 3.1.1). Opus cost is now small (Tier-3 only), but a hosted backend + index ops
  still favor recurring revenue.

---

## 5. Build phases
- **P0 — DONE (proof):** Apple Vision retrieves real phone photos top-1 (n=4); model comparison (Apple≈
  CLIP≈DINOv2, Apple wins on practicality); acquisition reality mapped; demand-weighted seeding 0→87%
  completeness (yours), scaling test on PankoVinyl 9k. Tooling in scratchpad.
- **P0.5 — GO/NO-GO: real-photo recall benchmark.** 300–1,000 user-shot sleeves across collectors/
  phones/lighting, in- & out-of-index, head-to-head vs Record Scanner. Report SEPARATELY: DB-coverage,
  top-1, top-5, false-accept, reject-rate, time-to-index. **This number decides the product.** (n=4 is
  not enough; the 54–58% mixed proxy is the warning.)
- **P1 — index + cold-start onboarding (only if P0.5 passes):** demand-weighted **label-pack** seed per
  scene (validated path) + CAA general fill; ANN service; **import-first onboarding** (Discogs login /
  wantlist / collection import / pick-your-labels) so a new user's index is pre-warmed — NOT blind
  scanning into an empty DB. OCR/catno carries cold-start scans.
- **P2 — TestFlight MVP:** SwiftUI camera + on-device Apple Vision embed + backend ANN + OCR/catno
  fusion + sleeve crop + decision-state confirm UI + manual fallback + enrichment. Measure first-session
  success (accepted matches, manual rescues, abandons, trust). Collect anonymized failure telemetry
  (separate "not in index" from "bad match") — NOT user-correction dependence.
- **P3 — monetize + grow:** StoreKit subscription; demand-driven index growth.
- **P4 — parity (optional):** Discogs commercial / Preferred-API-Partner data agreement.

---

## 6. Risks / open
- **Coverage ceiling without a Discogs deal (~10% general via CAA; niche grows on demand).** Biggest
  strategic constraint — decide if niche-first is acceptable or a data deal is required.
- **Phone-photo robustness** — proven at n=4 (top-1); needs sleeve crop + larger eval; angle/glare/dim
  shop will lower scores. Apple feature-print is the baseline; CLIP/DINOv2 may beat it on low-margin cases.
- **Same-artwork pressings** — retrieval finds the artwork family; OCR/catno/barcode disambiguates the
  exact pressing. Must be designed in, not bolted on.
- **False confidence** — use relative-margin scoring + confirm-among-candidates; never silent wrong guess.
- **Legal** — embeddings-not-images is materially safer but counsel before commercial launch.

---

## 7. Reuse vs build
| Reuse from ocdj | Build new |
|---|---|
| `services/discogs.py`, `spotify.py`, `youtube.py` (enrichment) | SwiftUI app + camera + sleeve crop |
| `services/claude_vision.py` (Opus, now Tier-3) | On-device embedding capture + ANN index/service |
| config-store pattern | CAA ingestion + MBID→Discogs mapping + demand-fill pipeline |
| | OCR-narrowing resolver + decision-state confirm UI + StoreKit |
