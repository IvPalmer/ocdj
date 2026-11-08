# Instagram pilot connector

Native OCDJ workspace at `/instagram`. The fixed Track Downloads collection
`18117783274346248` imports **five newest saved posts in response order**, with
all carousel children (up to Instagram's 20 per post). Images are navigable but
not included in the recognition denominator. Reviewed is local state only.

## Local verification

From repository root, create the Python environment and install both application
and optional browser QA dependencies:

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r tools/instagram/qa-requirements.txt
npm ci
npm run build
```

Use an isolated PostgreSQL cluster/database, with `POSTGRES_HOST`,
`POSTGRES_PORT`, `POSTGRES_USER`, and `POSTGRES_DB` pointing at that cluster.
Set `HUEY_DB_PATH` to a writable scratch file. Then:

```sh
.venv/bin/python backend/manage.py test instagram --noinput
.venv/bin/python -m unittest tools.instagram.test_connector -v
PYTHONPATH=. INSTAGRAM_BROWSER_TEST=1 .venv/bin/python backend/manage.py test instagram --noinput
```

The optional Chromium test requires `CHROME` and `PAPERCLIP_SCRATCH_DIR` (for
screenshots). It uses a temporary test DB and Django test server, synthetic
video and deterministic fixture recognition. It never connects to Instagram or
claims live Shazam QA. `fixture.json` has 5 posts, 18 videos and one image.

## Explicit Mac run

Install `tools/instagram/requirements.txt` into a Mac-owned virtual environment.
Use the **existing** `DRAIN_TOKEN` injection used by the drain connector; never
paste a token on the command line, into the repo, or into a task comment.

```sh
python tools/instagram/connector.py --api https://YOUR-OCDJ-ORIGIN --profile 'Profile 1' --work-seconds 600
```

No daemon, LaunchAgent, scheduled login or credentials are installed. A run
syncs the collection, uploads bounded videos, then polls for recognition jobs
for the specified window (0–3600 seconds). A job in progress finishes before
exit. Run again to refresh playback after expiry or process later requests.
The default window is zero: sync/upload and drain queued work, then exit.

`yt-dlp` reads Chrome cookies on Mac; only Instagram domains enter the
Instagram requests session. Cookies and CDN URLs remain in memory. The CDN
session is separate and cookie-free. No raw browser cookie export is created.
Only metadata, video bytes, and artist/title candidates go to OCDJ. Chrome
Keychain/profile access must already be available to the Mac agent.

`imageio-ffmpeg` supplies the temporary standalone binary that avoids the
broken Homebrew ffmpeg. Preflight runs `-version`. `INSTAGRAM_FFMPEG` can
explicitly select another working executable without altering Homebrew.
Recognition uses **Shazam only**, with 20-second samples starting at 0/30/60s;
transport/decoding failures become `error`, successful empty results become
`no-match`. Distinct artist/title candidates are retained. No ACRCloud calls.

The connector does not follow redirects with the bearer, Instagram session,
or CDN fetches. It stops on login expiry, restriction, or rate limit rather
than repeatedly probing Instagram; the next explicit run is the retry. Error
output deliberately omits exception bodies, cookies and signed URLs.

Offline demonstration (use an isolated local OCDJ instance, never production):

```sh
python tools/instagram/connector.py --api http://127.0.0.1:8002 --fixture tools/instagram/fixture.json --work-seconds 600
```

This uploads synthetic tone videos and labels the whole workspace as fixture.
Recognition of fixture jobs is simulated and clearly marked by the banner.

## State, leases and playback boundary

`/api/instagram/connector/{heartbeat,sync,claim}/` and per-media upload/complete
routes reuse `drain.auth.require_drain_token`, disabled without `DRAIN_TOKEN`.
Claims use PostgreSQL row locking/skip-locked, random fencing tokens and a
10-minute lease. Completion, corrections and wishlist use per-media locks.
Corrections invalidate in-flight results. Repeat recognition requests reuse
one bounded per-video queue slot. Sync updates IDs/positions/metadata while
preserving corrections, candidates, reviewed and wishlist links. Inactive
posts/children cannot be played or claimed.

Browser JSON/actions/streaming share the incumbent `/api/` Cloudflare Access
boundary, like the existing application APIs. **The Django origin must remain
private behind that boundary**; this feature does not introduce Django user
login or trust an unverified forwarded identity header. The connector paths
must be reachable through the existing authorized connector access setup;
never broaden the browser/media path into a public Access bypass. Security
review and live Mac verification must confirm the actual ingress rules before
release. No production config changes are made by this implementation.

MP4 uploads have a 64 MiB per-file limit and a 512 MiB aggregate cache limit.
Only server-generated opaque names are used; caller filenames are ignored.
Files live at `backend/.cache/instagram` by default, outside `MEDIA_ROOT` and
nginx static routes. Same-origin `/api/instagram/media/ID/stream/` supports
single ranges, suffix ranges and HEAD, with private/no-store and nosniff.
The server never proxies a caller URL. Playback expires after 24 hours. The
existing Huey worker cleans owned expired/inactive/orphan files every 30
minutes, and connector heartbeats/syncs also clean. A short 5-minute orphan
grace protects in-flight uploads. No music library paths are read or changed.
Production compose shares a dedicated `instagram_cache` volume between the
backend and existing Huey worker. Cache eviction is safe; the next explicit
Mac sync restores videos. Collection/review/wishlist state remains in PostgreSQL. There is no
standing URL-refresh service: the UI explains how to rerun the connector.

## Required live pilot before merge

Coordinate with existing GROA-553; do not create another Mac job. Use the same
pinned Chrome Profile 1 and collection. Confirm five posts/all children, play
and seek two different carousel videos from the VPS-hosted browser, identify
one with Shazam, correct it, add it once to wishlist twice, mark reviewed, and
resync to prove persistence. Check offline/session-expired/rate-limited status
and the actual Access/media boundary. Attach live evidence separately from
fixture screenshots. Last reviewer merges after checks; production deployment
is a separate operator decision.
