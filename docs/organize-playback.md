# Organize playback (#13)

Organize's ordinary `/organize` list now offers shared Play/Queue controls for
server-resident browser audio files. Each serialized pipeline row supplies an
ID-only `/api/organize/pipeline/<id>/audio/` URL or an unavailable reason. Controls
keep that specific pipeline source even if the artist/title matches a Mac index
record. The existing persistent player owns playback, queue, navigation and seek.

The endpoint reuses `resolve_artifact_path` (including stale work_path fallback),
requires a nonempty regular file in `SOULSEEK_DOWNLOAD_ROOT` or the configured
`OCDJ_PUBLISH_ROOT`, and opens every path component without following symlinks.
GET/HEAD support one HTTP byte range, suffix/open-ended ranges, 206/416, private
no-store and inline audio. It accepts no filesystem path argument. Archived,
missing, unsupported and unsafe files have no stream URL. Changes after queueing
return 410 and the existing player error/retry UI. An already-open descriptor can
finish playing after a rename/unlink. There are no writes, conversions, leases,
new dependencies, migrations, or changes to signed travel downloads.

Allowed containers: MP3, WAV, M4A, AAC, FLAC, Ogg and Opus. Decoder support still
depends on browser/codec; unsupported content uses the player's existing error
state. AIFF and other containers remain unavailable here; conversion is outside
this read-only slice.

## Verification and release handoff

`.github/workflows/organize-player.yml` builds the actual frontend and runs CI-only
PostgreSQL + temporary WAV fixtures. Backend tests enter through the real pipeline
list/detail, follow the emitted URL, check byte ranges/HEAD, root and symlink
refusals, stale-path fallback, archival/disappearance and unchanged rows/bytes.
Chromium and WebKit open the built app's ordinary `/organize` route against the
real synthetic Django list/stream. They exercise Play, Queue without eager media
fetch, Settings navigation, pause, native seek, Next/Previous and a browser Range
fetch. Library-match responses are synthetic and intentionally match another ID
to check that Organize retains its own source. No local Django/API/DB is needed.

Parent owns review/merge and the plain-compose release in RUNTIME.md. Before
releasing, verify the VPS's **external** ingress routes still protect the new
`/api/organize/pipeline/<id>/audio/` path with the normal oauth2-proxy login. App
authentication is provided by that existing ingress, as for ordinary list/detail;
Django fixture access alone is not authentication evidence. Never add this route
to the signed-download or machine/bearer exemptions, and never expose the origin.
The ingress config is outside this repository; no runtime/ingress access was
performed by this delegated agent.

After an authorized release, confirm anonymous GET and Range GET both return 401,
then authenticated list → Play/Queue of a real supported pipeline row, 206 seek,
page navigation, Next/Previous, and a missing/archived item. Record deployed SHA
and browser/format coverage. No migration or connector activation is required.
Rollback is the parent's normal previous-image/source rollback; no data undo.
