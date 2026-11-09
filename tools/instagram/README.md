# Instagram collection connector

Native OCDJ workspace at `/instagram`. The fixed Track Downloads collection
`18117783274346248` imports the **complete saved collection in response order**, with
all carousel children (up to Instagram's 20 per post). Images are navigable but
not included in the recognition denominator. Reviewed is local state only.

## Verification (VPS-only runtime)

`RUNTIME.md` prohibits starting a local DB/API. Run `npm ci && npm run build`
and the DB-free connector tests locally. The GitHub `Instagram fixture` workflow
provides isolated PostgreSQL, frontend build, migration consistency, backend and
Chromium fixtures; it never connects to Instagram. No local server is needed.
The five-post fixture remains a synthetic demonstration; cursor tests also cover
256 posts/1,232 video children over 22 synthetic pages. This is not live QA.

```sh
python -m unittest tools.instagram.test_connector -v
```

## Explicit Mac run

Install `tools/instagram/requirements.txt` into a Mac-owned virtual environment.
Use the **existing** `DRAIN_TOKEN` injection used by the drain connector; never
paste a token on the command line, into the repo, or into a task comment.

```sh
python tools/instagram/connector.py --api https://YOUR-OCDJ-ORIGIN --profile 'Profile 1' --work-seconds 600
```

No daemon, LaunchAgent, scheduled login or credentials are installed. A run
syncs the complete metadata snapshot, then polls for selected-media/recognition jobs
for the specified window (0–3600 seconds). At most ten jobs run per invocation; a job in progress finishes before
exit. Run again to refresh playback after expiry or process later requests.
The default window is zero: metadata sync and up to ten queued jobs, then exit.

`yt-dlp` reads Chrome cookies on Mac; only Instagram domains enter the
Instagram requests session. Cookies and CDN URLs remain in memory. The CDN
session is separate and cookie-free. No raw browser cookie export is created.
Only metadata, video bytes, and artist/title candidates go to OCDJ. Chrome
Keychain/profile access must already be available to the Mac agent.

`imageio-ffmpeg` supplies the temporary standalone binary that avoids the
broken Homebrew ffmpeg. Preflight runs `-version`. `INSTAGRAM_FFMPEG` can
explicitly select another working executable without altering Homebrew.
Recognition first checks explicit caption IDs and bounded comment suggestions,
then uses Shazam with 20-second samples starting at 0/30/60s;
transport/decoding failures become `error`, successful empty results become
`no-match`. Distinct artist/title candidates are retained. No ACRCloud calls.

The connector does not follow redirects with the bearer, Instagram session,
or CDN fetches. It stops on login expiry, restriction, or rate limit rather
than repeatedly probing Instagram; the next explicit run is the retry. Error
output deliberately omits exception bodies, cookies and signed URLs.

Offline demonstration (CI or authorized isolated VPS QA only, never local DB/API
or production):

```sh
python tools/instagram/connector.py --api http://127.0.0.1:8002 --fixture tools/instagram/fixture.json --work-seconds 600
```

Only fixture mode uploads synthetic tone videos eagerly. Live mode imports
metadata, then downloads/uploads only explicitly requested selected media.
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

Browser JSON/actions/streaming use Django `AllowAny`: they require the
incumbent **outer browser authentication and a private origin**. Do not infer
protection from this Django code or the internal nginx proxy. The repository's
`docs/ROADMAP.md` D4/T-005 records oauth2-proxy, not Cloudflare Access, as the
deployed browser model. The historical routing receipt is commit
[`2b682499d36d3ae9dc6b966933f0f2918b5357de`](https://github.com/IvPalmer/ocdj/commit/2b682499d36d3ae9dc6b966933f0f2918b5357de):
`/api/drain/*` uses `DRAIN_TOKEN`, while the exact POST
`/api/organize/pipeline/kick/` has a separate Traefik priority-300 router and
requires **KICK_TOKEN**. That token does not authenticate Instagram endpoints.
The receipt locates host-managed routing in
`/etc/dokploy/traefik/dynamic/ocdj-auth.yml`; that file is not in this checkout.
These are source/history facts, **not current live ingress verification**.

No existing rule shown here establishes reachability for the new
`/api/instagram/connector/` namespace. A working drain route does not prove
that these routes can bypass browser login. The connector sends only the
existing `DRAIN_TOKEN` bearer and refuses redirects; an oauth2-proxy login
redirect means it cannot run through that URL. Do not add a public browser/media
bypass, reuse KICK_TOKEN, copy browser login cookies, provision access, or change
production routing to make the pilot pass. Have the existing GROA-553 lane and
project lead verify the already authorized private access path; if none exists,
record the exact missing route/access and keep security acceptance blocked.

Before security resubmission, attach a sanitized live boundary matrix from the
existing access path (record target, UTC time, method, status, serving layer and
tested commit; omit credentials, cookies, signed URLs and raw login bodies):

| Probe | Required evidence |
| --- | --- |
| Anonymous `GET /api/instagram/`, `HEAD /api/instagram/media/ID/stream/`, and browser action requests | Denied by the outer browser boundary, without JSON, bytes, or state mutation |
| Direct backend/frontend origin, including their API/media paths | Unreachable to an external unauthenticated client or explicitly denied; no path around browser auth |
| Existing authorized Mac bearer to connector sync with an invalid empty payload | Django validation rejection (400), proving reachability/auth without ingesting or claiming work |
| Missing and invalid bearer to each connector route | Django rejection (401); 503 if DRAIN_TOKEN is disabled, including when outer routing is reachable |
| Existing Mac bearer sent to browser JSON/media/action routes | Still requires browser authentication; connector access grants no browser bypass |
| Authorized browser to JSON/media/actions on an approved test instance | Successful listing, two-child playback/seek and scoped fixture/live actions |

Django fixture tests prove only the in-app bearer checks. A login redirect can
prove a particular browser denial but cannot prove connector reachability.
Network errors, a 404 on an undeployed route, and historical configuration prove
neither the live boundary nor end-to-end success. No production configuration
changes are made by this implementation.

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
and the actual oauth2-proxy/origin/media boundary using the matrix above. Attach live evidence separately from
fixture screenshots. Last reviewer merges after checks; production deployment
is a separate operator decision.

## Full metadata and selected sources

The cursor walk is capped at 120 pages/2,000 unique posts, preserving first-seen
saved order. Missing/repeated cursors while more pages remain fail the run before
sync, preserving the previous snapshot. Every post uses the same native model/UI.
There is no separate link index. Selected image children use the same private
cache, while only video children can be recognized.

`POST /api/instagram/media/{id}/playback/` requests a source. Claims include an
explicit action, parent source ID and position. The connector refreshes one post
for a signed CDN URL rather than rereading the entire collection per job. No
signed URL is persisted. Public browser routes still require the existing OAuth
boundary; connector bearer calls require the already authorized private tunnel.

Cached video recognition dispatches to the existing VPS Huey worker and uses the
same bounded ffmpeg/Shazam function as the connector. This needs no manual play
or live Mac. Uncached private source access still needs an explicit authorized Mac
run. A stopped connector is offline after the existing heartbeat timeout; this
change installs no scheduler or standing service.

See [the recognition contract](../../docs/instagram-recognition-contract.md) for
source evidence, #16 integration gates, live verification blockers and remaining
#13 library/player work. Neither this README nor a successful fixture run grants
release authorization.
