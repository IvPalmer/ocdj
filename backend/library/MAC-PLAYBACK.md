# Issue 13: private on-demand indexed Mac audio

Release base: `vps-deploy` at `4aaa283f5dfe8e11a499f9d37b0e2de2802b2ec0`.
Parent owns review, integration, activation, deployment and live QA.

## Flow and boundaries

An unambiguous exact artist/title Mac snapshot match adds Play library / Queue
library beside the existing quality/Finder indicator. Other previews remain
available. Selecting that source in the persistent player POSTs the indexed
numeric record ID to `tracks/<id>/playback/`. Queued future items do not stage.
The server resolves the record to its exact collection UUID + opaque HMAC ID.
No client path, URL, glob, search, or filename becomes a source.

The explicitly run `tools.mac_library.helper` claims one request at a time using
the existing DRAIN_TOKEN over a caller-owned loopback SSH forward to the private
backend. It requires an explicit root identical to the existing private map.
It opens only exact map entries beneath that root with descriptor-relative,
non-following opens; rejects traversal, staging/archive directories, symlinks,
nonregular files, changed files and oversized input. A validated descriptor goes
to an already installed FFmpeg with a fixed demuxer and file/pipe protocol
allowlist. It produces a metadata-stripped 192 kbps stereo MP3 playback copy;
indexed original quality labels are unchanged and the player labels the copy.

This is a Python CLI using its user's existing filesystem access, with explicit
root enforcement in code. It does **not** inherit the native helper's sandbox
grants, change entitlements, install an artifact, or require native signing.
The parent must confirm that its launch context can read the private map and
the explicitly selected root. If macOS denies access, stop; do not grant Full
Disk Access or broaden privileges as a workaround.

The VPS keeps only staged MP3s in `/app/.cache/mac-playback`, outside MEDIA_ROOT
and nginx routes, with mode 0600. Defaults are 512 MiB source, 120 seconds per
transcode, 64 MiB per MP3, 256 MiB total staging, 3 minute request/lease,
30 minute cache, and a 15 minute connector run (maximum 30 minutes, plus at most
one in-flight transcode/upload). Output exceeding the cap is refused, not used
as a truncated song. Uploads hold a process-wide file lock through DB commit;
stale/expired leases cannot publish or replace a cache entry. Aborted uploads
remove partials; a process crash may leave bounded orphan staging, removed by
lazy expiry cleanup on the next upload. Expired bytes are immediately
unservable and physically removed on the next upload, or manual purge below.
Cache resides in the backend container layer and may disappear on redeploy;
the next explicit Play recreates it. No durable audio-volume change is needed.

Browser preparation/status/audio retain the existing oauth2-proxy protected
ingress and private origin assumptions. Django's ordinary browser endpoints
use AllowAny, as elsewhere in this app: this change does not add standalone
browser authentication. Connector routes add the existing bearer decorator
(503 with no server token, 401 without a valid bearer). **Do not add a public
Traefik exemption**: the connector reaches these routes privately through SSH.
No token enters a browser, URL, source map, log, issue or PR. Audio supports
GET/HEAD and a single bounded byte Range including suffix and open-ended forms;
malformed/multiple/unsatisfiable ranges return 416. All media uses private,
no-store and nosniff. No public Mac listener, CORS exception or file:// link.

Offline and local-source failures do not mark the library file missing. The
player retains the current track, queue and controls; retry is explicit. The
connector reports only a sanitized local failure, and stops on any transport
error/ambiguous result without resending. There is no automatic relaunch,
LaunchAgent, permanent polling service, new dependency, or native installation.
Pause during preparation still allows that already requested copy to finish.

## Parent operations (not executed by this change)

1. Review/merge to the release branch, build/deploy through the existing VPS
   runbook. Migration 0003 adds only the `MacPlayback` request/cache table.
2. Validate that the backend origin remains private and anonymous external
   requests to **all** new browser and connector paths receive ingress 401;
   confirm logged-in browser GET/POST/audio work. Do not weaken ingress.
3. Determine the backend container's private Docker IP on the VPS using the
   existing deployment inventory/`docker inspect` (IPs change on redeploy).
   Open an operator-owned foreground tunnel, for example:

   ```sh
   ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=2 \
     -L 127.0.0.1:18032:<verified-private-backend-IP>:8002 main-instance
   ```

   This binds only Mac loopback; it does not publish the VPS backend. The
   remote target must be the verified container IP, not a public origin or a
   guessed host-loopback port. Existing compose does not publish backend 8002.
4. In a separate terminal, inherit the existing bearer environment without
   printing it. Use an existing Python environment with requests + mutagen
   (already used by the snapshot/connector tooling). From the repo root:

   ```sh
   PYTHONPATH=backend:. python -m tools.mac_library.helper \
     --api http://127.0.0.1:18032 --root '<exact-existing-snapshot-root>' \
     --ffmpeg '<verified-existing-ffmpeg-binary>' --seconds 900
   ```

   Map defaults to the existing sandbox-side reveal-map; `--map` can select a
   private existing map. Root mismatch/permissions/transport fail closed.
   No key is needed/read. Check FFmpeg `-version` first: this Mac's Homebrew
   binary failed in development with a missing x265 dylib; fixtures used the
   already installed imageio_ffmpeg binary. Do not assume system FFmpeg works.
5. Live QA with one known local file: Library normal entry, quality and Finder,
   Play, Queue, seek/Range, next/previous, pause, routes, expired-source retry,
   Mac offline, stale map, and Safari/Chrome playback under real browser auth.
   Only synthetic browser/codec evidence is provided by this PR; physical
   library access, production ingress and actual Safari/Chrome sessions remain
   parent QA. The connector is deliberately bounded and must be explicitly
   started again for a later listening session.

## Rollback

Stop the foreground connector/tunnel. Revert this PR and redeploy the previous
release SHA through the parent runbook. The additive table can remain unused;
do not reverse/drop it as an emergency rollback. Delete **only** the backend's
`/app/.cache/mac-playback` directory after stopping the connector if immediate
byte removal is needed. No source audio, snapshot, matching/Finder map, Wanted
state, native app, browser extension or LaunchAgent is modified.

## Proof

Pure fixtures cover map scope, exact ID lookup, traversal/symlink/FIFO/missing/
oversized/changed-file rejection, actual WAV-to-MP3 encoding, output caps,
private transport URL/redirect/proxy restrictions, and no resend after ambiguous
upload. CI PostgreSQL covers normal prepare/claim/upload/status/audio, bearer
fail-closed, collection isolation, stale completion, quota/invalid media,
expiry, missing records/cache, retry, HEAD and Range. Browser fixtures enter
through the normal Library UI with synthetic API responses and real synthetic
MP3, checking Finder, prepare/offline/retry, play/pause, queue/next/previous and
route continuity. CI runs Chromium and WebKit; neither equals live Safari QA.
