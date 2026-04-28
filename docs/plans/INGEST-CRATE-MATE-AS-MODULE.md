# Ingest crate-mate into ocdj as a module

Drafted 2026-04-28. Status: PLAN. Not yet executed.

---

## Goal

Fold `IvPalmer/crate-mate` (album-cover recognition + multi-platform metadata enrichment) into ocdj as a first-class module — a new sidebar route `/cratemate` backed by a Django app that exposes REST endpoints. Retire the standalone crate-mate repo + the HuggingFace Spaces + Streamlit entrypoints. Crate-mate's migration to the VPS (originally tracked in `elder-brain/docs/MIGRATION-PLAN.md`) is replaced by this absorption.

## Survey

### crate-mate (current)

- Three parallel entrypoints — none of them clean. To keep:
  - `app.py` (Streamlit) — the working app, but Streamlit is the wrong substrate for an ocdj module. **Drop.**
  - `backend/app/` (FastAPI) — `main.py`, `routes/`, `collectors/`, `universal_recognizer.py`, `hybrid_search.py`, `simple_universal_search.py`. **This is the keeper.** Refactor into a Django app.
  - `frontend/src/` (React + axios) — small, easy to port. Merge as a route into ocdj's existing Vite/React app.
- Capabilities (from README + code):
  - Album-cover identification via Gemini Vision API.
  - Metadata enrichment: Discogs, Spotify, YouTube, Bandcamp.
  - Tracklist + per-track streaming links + market pricing.
  - Optional ResNet18 embedding model (`backend/resnet18_tuned.pth`, 45 MB) for similarity / fallback recognition.
- Sensitive files **already committed** (must purge before merging into ocdj):
  - `backend/gcp-credentials.json` — empty file today, but historic blobs likely contain the real key.
  - `neural-quarter-470623-r2-e3891a759a91.json` — 2.4 KB GCP service account JSON.
  - `backend/resnet18_tuned.pth` — 45 MB binary; never belongs in git regardless of secrets.
- Remotes: `origin` → `github.com/IvPalmer/crate-mate`, `hf` → `huggingface.co/spaces/ivpalmer42/crate-mate`. Branch `vps-deploy`.

### ocdj (current target)

- Django (DRF) + Huey + React (Vite) + nginx, Dokploy-managed (`ocdj.grooveops.dev`).
- Existing Django apps: `core`, `wanted`, `soulseek`, `traxdb`, `recognize`, `organize`, `dig`, `library`, `drain`.
- `recognize` is **audio** recognition (ACRCloud, trackid, segmenter, clustering) — overlapping concern, different modality. Don't conflate.
- React routes: `/dashboard /wanted /soulseek /traxdb /recognize /organize /library /agent /settings`. Pattern is clear: each Django app maps to a sidebar route.
- Cloudflare Access policy already drafted in `docs/CLOUDFLARE-ACCESS-SETUP.md` — gates `/api/drain/*` and `/api/organize/pipeline/kick/`. New cratemate endpoints should sit under the protected app surface by default.

## Architectural decisions

### Module shape: new Django app `cratemate`, not an extension of `recognize`

Reasoning: `recognize` is audio recognition (ACRCloud, fingerprinting). Crate-mate is image recognition + DSP-metadata aggregation. Different inputs, different external services, different DB models. A separate app keeps testing surfaces small and the URL prefixes obvious (`/api/cratemate/...`). Future "shared identification result" abstraction can live in `core` if needed.

**Layout inside ocdj/backend:**

```
backend/cratemate/
  __init__.py
  apps.py
  models.py            # AlbumIdentification, IdentifiedRelease, RecognitionRun
  serializers.py
  views.py             # DRF ViewSets
  urls.py              # mounted at /api/cratemate/
  services/
    gemini_vision.py
    discogs.py
    spotify.py
    bandcamp.py
    youtube.py
    universal_recognizer.py   # orchestrator (was crate-mate's universal_recognizer.py)
    hybrid_search.py          # was crate-mate's hybrid_search.py
    embeddings.py             # ResNet18 wrapper, lazy-loaded
  tasks.py             # Huey: long-running enrichment as background tasks
  migrations/
  tests/
```

### Frontend integration

- Add route `/cratemate` in `src/App.jsx`. New component `src/components/cratemate/CratematePanel.jsx` (port of `crate-mate/frontend/src/components/`).
- Consume `/api/cratemate/identify/` (POST image), `/api/cratemate/results/<id>/` (GET enriched result). Reuse ocdj's `src/api/` axios setup; drop crate-mate's `axiosConfig.js`.
- Sidebar nav: add `Crate-Mate` entry between `Recognize` and `Organize`.

### Model weights (45 MB)

`resnet18_tuned.pth` cannot live in git. Two viable paths:

- **(A) Build-time fetch** — store the file in a private B2 bucket; ocdj's Dockerfile downloads it at build time using a token from build-args. Reproducible, no runtime fetch latency.
- **(B) Volume mount** — keep the file out of the image entirely; mount a Dokploy named volume `cratemate_models` and seed it once per environment via SSH.

**Recommendation: (A).** Fewer moving parts, image is self-contained. Use the same B2 bucket reserved for backups.

### Sensitive credential handling

- **Must run `git filter-repo` on a fresh `crate-mate` clone before any code lift** to scrub `gcp-credentials.json` + `neural-quarter-*.json` from all branches and tags. Reference SHAs to identify in the purge list.
- The actual GCP key gets rotated after purge regardless (assume it leaked). New service-account JSON goes into `~/.secrets/ocdj-cratemate.env` on Mac, mirrored to Dokploy env in encrypted form.
- ocdj already loads env via `os.environ` — add `CRATEMATE_GEMINI_API_KEY`, `CRATEMATE_DISCOGS_TOKEN`, `CRATEMATE_SPOTIFY_CLIENT_ID/SECRET`, `CRATEMATE_GCP_SA_JSON` (base64-encoded JSON, decoded at boot in `services/gemini_vision.py`).

### Auth / CF Access

- Ingested module inherits ocdj's policy. Default: protected — same as the rest of `/api/`.
- Public-readable variant (anonymous album-cover lookup, no DB writes) is a **V2 question** — easy to add as a separate URL prefix `/api/cratemate/public/` with rate limiting and CF Access bypass, mirroring how `/api/drain/*` is bypassed in `CLOUDFLARE-ACCESS-SETUP.md`. Defer.

### Streamlit + HF Spaces fate

- Streamlit `app.py` and `streamlit_app.py` — **delete** during the lift. They duplicate the FastAPI logic and don't survive the Django port.
- HuggingFace Space `ivpalmer42/crate-mate` — **archive** (HF Spaces has a "pause" toggle). Once ocdj `/cratemate` is verified live, push a final commit on the HF remote replacing `app.py` with a 10-line redirect to `https://ocdj.grooveops.dev/cratemate`. Keep the Space repo (free, low maintenance) so existing inbound links don't 404.

## Phased plan

### Phase 0 — confirmation gates (15 min)

- [ ] Confirm decision to retire the standalone crate-mate repo (vs keep both code paths). **Default: retire**, since duplicated business logic across two repos is the fastest way to drift.
- [ ] Confirm B2 vs Dokploy volume for the 45 MB model.
- [ ] Confirm: rotate the GCP key after purge regardless of whether the empty-on-disk file ever held a real key in history.

### Phase 1 — purge crate-mate history (1 hr)

- [ ] Fresh clone of `IvPalmer/crate-mate` to a scratch dir (don't reuse the working copy).
- [ ] `git filter-repo --path backend/gcp-credentials.json --invert-paths --path neural-quarter-470623-r2-e3891a759a91.json --invert-paths --path backend/resnet18_tuned.pth --invert-paths`.
- [ ] Force-push purged branches to origin (`vps-deploy`, `main` if it exists). Notify any collaborators (probably none).
- [ ] Verify with `git log --all -- backend/gcp-credentials.json` returns nothing.
- [ ] Rotate the GCP service-account key in GCP console; revoke old key.

### Phase 2 — code lift (3–4 hr)

- [ ] In ocdj on a new branch `feat/cratemate-module`:
  - [ ] `python manage.py startapp cratemate`. Move generated app under `backend/cratemate/`.
  - [ ] Add `'cratemate'` to `INSTALLED_APPS`.
  - [ ] Port `crate-mate/backend/app/main.py` routes into `cratemate/views.py` (FastAPI → DRF). Each FastAPI route becomes a DRF action; pydantic models become DRF serializers.
  - [ ] Port `collectors/` modules into `cratemate/services/`. Each one is a thin wrapper around an external API; rewrite as plain modules with dependency-injected `requests.Session` so tests can mock.
  - [ ] Port `universal_recognizer.py` + `hybrid_search.py` into `cratemate/services/`.
  - [ ] Define Django models: `AlbumIdentification` (input image hash, gemini result, timestamp, user FK), `IdentifiedRelease` (enriched metadata), `RecognitionRun` (audit log linking input → result).
  - [ ] Wire URLs at `/api/cratemate/`. Add to `backend/djtools_project/urls.py`.
  - [ ] Add Huey tasks for the slow path (Discogs lookup, Bandcamp scrape) so the HTTP request returns immediately with a job ID.
- [ ] Port the React component:
  - [ ] Copy `crate-mate/frontend/src/components/*` → `ocdj/src/components/cratemate/`. Strip axios config; wire to ocdj's existing API client.
  - [ ] Add `<Route path="/cratemate" element={<CratematePanel />} />` to `src/App.jsx`.
  - [ ] Add sidebar nav entry.
- [ ] Add deps to `backend/requirements.txt`: `google-generativeai`, `discogs-client`, `spotipy`, `beautifulsoup4`, `lxml`, `fuzzywuzzy`, `python-Levenshtein`. Skip `streamlit`, `pytesseract`, `opencv-python-headless` unless a concrete use is identified post-port.
- [ ] Add `torch` + `torchvision` only if the ResNet path is actually wired in V1. **Default: defer the ResNet path to V2** — Gemini Vision alone covers the happy path.

### Phase 3 — model weights (skip in V1 if ResNet deferred, 1 hr otherwise)

- [ ] Upload `resnet18_tuned.pth` to private B2 bucket `ocdj-models`.
- [ ] Add `B2_KEY_ID` + `B2_APPLICATION_KEY` build-args to ocdj's `Dockerfile`.
- [ ] Dockerfile snippet: `RUN b2 download-file-by-name ocdj-models resnet18_tuned.pth /app/models/resnet18_tuned.pth`.
- [ ] `cratemate/services/embeddings.py` reads from `/app/models/resnet18_tuned.pth`, lazy-loads on first call.

### Phase 4 — env + secrets (30 min)

- [ ] Generate new Discogs/Spotify/YouTube/Gemini keys (or rotate existing).
- [ ] Write `~/.secrets/ocdj-cratemate.env` on Mac.
- [ ] Set the same vars in Dokploy env for the ocdj application.
- [ ] Add a startup check in `cratemate/apps.py` that warns (not crashes) if a key is missing — module degrades gracefully.

### Phase 5 — migrations + smoke test (30 min)

- [ ] `python manage.py makemigrations cratemate && python manage.py migrate`.
- [ ] Local: upload an album cover via `/cratemate`, confirm result.
- [ ] Verify CF Access still gates the page from a logged-out browser.
- [ ] Run the existing ocdj test suite to ensure nothing regressed elsewhere.

### Phase 6 — deploy + retire (30 min)

- [ ] Push `feat/cratemate-module` → open PR → merge to `vps-deploy`.
- [ ] Dokploy auto-redeploys. Verify on `https://ocdj.grooveops.dev/cratemate`.
- [ ] On HuggingFace, push a redirect-only `app.py` to the Space.
- [ ] On `IvPalmer/crate-mate`: archive the repo (GitHub Settings → Archive). Update its README with a single line pointing to `ocdj/cratemate`.
- [ ] Update `elder-brain/docs/MIGRATION-PLAN.md` and `elder-brain/wiki/manifest.yaml` — remove crate-mate as a separate slug; add a note in `elder-brain/wiki/decisions/2026-04-28-cratemate-into-ocdj.md`.

## Effort summary

| Phase | Time | Notes |
|---|---|---|
| 0 — gates | 15 min | three decisions |
| 1 — history purge + key rotation | 1 hr | one-shot, irreversible |
| 2 — code lift | 3–4 hr | the bulk |
| 3 — model weights | skip in V1 | defer ResNet, Gemini-only first |
| 4 — env/secrets | 30 min | reuse Dokploy env mechanism |
| 5 — migrations + smoke | 30 min | local then VPS |
| 6 — deploy + retire | 30 min | mostly waiting |
| **Total** | **~6 hr** | one focused session |

## Open questions

1. **Public-readable variant?** Anonymous album-cover lookup (rate-limited, no DB writes) is a possible draw — defer or include in V1?
2. **ResNet path** — keep the model? Tests in crate-mate's `tests/` should clarify whether it actually fires today; if dead code, drop it entirely.
3. **HF Space redirect or full delete?** Keeping it as a redirect costs nothing; deleting risks breaking inbound links. Lean: redirect.
4. **Sidebar position** — between `Recognize` (audio) and `Organize` reads as a pipeline, but `Recognize > Cratemate` could be confusing (both about identification, different modality). Alternative: under a `Discover` group with a future image-search sibling.
5. **Where does this live in elder-brain's tracking?** Once merged, crate-mate stops being a separate slug. Update `wiki/manifest.yaml` accordingly and add a decision note.

## Out of scope for V1

- The ResNet18 fallback (defer).
- Mobile-friendly responsive polish from crate-mate's Streamlit (re-do natively in the React port — don't port Streamlit CSS).
- Any "share identified album to Discogs collection" write-side feature.
- Cross-module integration (e.g. "send a cratemate result into the wanted-list module") — rich integration is V2 once the module is healthy on its own.
