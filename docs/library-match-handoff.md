# Indexed library match — issue #13

Stacked on player PR #25, base `52ab836` (`codex/13-shared-bottom-player`).
This is a small read-only index lookup and shared track indicator. No migrations,
new dependencies, recognition backend changes or player core changes.

`GET /api/library/match/?artist=…&title=…` reuses LibraryTrack. Both complete
fields (up to 500 characters) are required. Matching lowercases and trims ASCII
outer spaces; punctuation, accents, inner spacing, featured artists and version
qualifiers remain significant. No fuzzy, substring or version-stripping matches.
The database lookup selects only ID, format, bitrate and sample rate, caps results,
and returns `coverage: indexed_only`. It never scans, stats, opens or modifies files.
It uses the existing `/api/` ingress; no authentication/ingress changes are made.

States: `matched`, `multiple_matches`, `indexed_missing`, `no_indexed_match`.
Marked-missing rows cannot supply available quality. Multiple nonmissing rows do
not choose a preferred version or quality winner. No indexed match is not proof
of absence from any wider collection. Query failures remain unknown in the UI.

The current scanner indexes the configured ready directory, not the operator's
whole Mac library. All indicators visibly say **Unindexed collections unknown**.
An indexed match means an exact metadata pair with `missing=False` in the stored
index; it is not a current physical-file check or canonical recording identity.
Format comes from the indexed extension. Positive stored bitrate/sample rate are
file measurements, shown verbatim in kbps/kHz; unavailable measurements say
**Quality unknown**. No guessed bitrate, bit depth, lossless provenance or full
inventory assertion is made. Manually corrected tags do not establish identity.

The indicator lives in TrackPlayback, so Wanted, recognized/Shazam and description
tracks, Library, imports, Crate-Mate and existing Instagram shared controls reuse
it. Duplicate metadata pairs share React Query cache for one minute; scan and tag
update success invalidate matches. Lookup is read-only and does not alter any
review/Wanted status, queue action or supplied playback source.

## Browser audio follow-on

Source inspection of Library serializers/URLs and nginx.prod.conf found no
library media endpoint: metadata/local paths are not audio sources. No live
API, inventory, media, filesystem or ingress verification was attempted.
The existing Instagram cached-media endpoint serves its own bounded cache only;
it cannot safely stand in for arbitrary indexed LibraryTrack files.

Small follow-on: add an ID-addressed `GET /api/library/tracks/<id>/audio/` behind
the authenticated ingress, resolve the indexed path server-side within the
configured allowed root, reject missing/outside-root/symlink escapes, and support
HEAD/byte ranges and private caching semantics without returning filesystem paths.
Test these boundaries using generated audio and temporary fixture roots, then
return a same-origin `playback_url` and pass it through existing shared controls.
Unsupported codecs need an explicit unavailable response unless bounded transcoding
is separately implemented. Verify ingress protection before release. No endpoint,
public media mapping, invented source or library Play button is added here.

## Validation and release

Local commands (no DB/API):

- `PYTHONPATH=backend python3 -m unittest library.test_matching -v`
- `npm ci --no-audit --no-fund` and `npm run build`
- Vite frontend only, then `python3 tools/test_library_match.py http://127.0.0.1:5187`
- `python3 tools/test_bottom_player.py http://127.0.0.1:5187`
- `git diff --check`

The new UI fixture intercepts API responses and blocks external requests. It
covers Wanted/Shazam, measured/unknown quality, duplicates, missing/no match,
failed requests, incomplete metadata, cross-surface cache reuse and absence of
invented playback/writes. Synthetic mobile/desktop screenshots were inspected.
The existing player fixture checks native synthetic playback, persistence and
provider adapters. Actual external providers and physical audio remain unverified.

CI extends the existing disposable PostgreSQL job with `library` tests and the
synthetic library UI fixture. The DB endpoint tests cover exact normalization,
version protection, missing rows, bounded duplicate results, invalid input,
read-only behavior and the path-free response contract. No local DB/API is used.

Self-review only. Draft review against #25's branch. #13 remains open for wider
inventory coverage, private library playback and remaining integration/release
work. No merge/deploy. Rollback removes this slice; no data changes to undo.
