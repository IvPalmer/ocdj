# Mac library metadata snapshot — #13

Draft slice stacked on #27 (`03ddcd5`). Manual metadata export and private
management-command import reuse `LibraryTrack` and its existing exact artist/title
matcher. No player, Instagram, ingress, credential or dependency changes.

## Contract and scope

`tools/mac_library/snapshot.py --root "$LIBRARY_ROOT"` is an aggregate-only,
read-only dry run. It does not load Django, start a DB/API, read a Music.app DB,
contact a service, save per-track data, or copy/edit audio. It reads Mutagen tags
and technical info from regular files under the explicit root. Supported
extensions: MP3, FLAC, AIFF/AIF, WAV, OGG, M4A. Artist/title come from tags, including
WAV/AIFF ID3 fallback; there is no filename guessing. Untagged readable tracks
remain in the snapshot with blank identity and cannot match an identified pair.

Directories named ID3 or TraxDB (case-insensitive) at any depth are excluded,
as are symlink files/directories. Roots inside staging/archive are refused.
This is a library inventory scope, not an ID3 download/staging or TraxDB archive
inventory. One excluded subtree can contain both staging and archive: its
contents are intentionally not traversed or counted. Unsupported extensions are
outside coverage. Coverage remains `indexed_only`, never whole-collection absence.

JSON v1 has exactly `version`, `collection_id` (canonical UUID), `captured_at`
(UTC timestamp with six fractional digits), `complete: true`, `tracks`.
Each track has exactly `id`, `artist`, `title`, `format`, `bitrate` (kbps or null),
`sample_rate` (Hz or null), `duration_seconds` (or null), `file_size_bytes`.
No filenames, paths, artwork, audio, URLs, cookies, or arbitrary tags are exported.
Format records the extension, not lossless provenance. Quality is measured, not
proof of recording identity or a current playable copy on the VPS.

Track IDs are HMAC-SHA256 of collection UUID + relative path using a private
32-byte local key. Keep the same root/scope, UUID and key between exports.
A root relocation retaining relative paths preserves IDs; renames become new
IDs. Do not reuse a collection UUID for another root/scope or rotate its key as
an ordinary refresh. No key or local path mapping reaches the importer.

Export is explicitly opt-in:

```sh
python tools/mac_library/snapshot.py --root "$LIBRARY_ROOT" \
  --collection-id "$COLLECTION_UUID" --key-file "$PRIVATE_KEY_FILE" \
  --export "$NEW_PRIVATE_SNAPSHOT_JSON"
```

Use the existing Mutagen 1.47.0 requirement. The key file must already exist,
contain exactly 32 random bytes, belong to the current user, and deny group/other
access. Provision it manually outside the repo; the tool never changes credentials.
JSON contains private musical metadata despite being path-free: keep it outside
Git/logs/public artifacts. Output is exclusively aggregate counts; parser/OS
exception text is suppressed. No per-file diagnostics or public inventory dump.

The exporter validates the entire payload, writes a mode-0600 temporary file,
then atomically links it to a **new** destination. Existing files/symlinks are
never overwritten. Failed publication removes only its own temporary artifact.
The scanner excludes empty files and valid ID3-only MP3 placeholders with fewer
than four bytes beyond the tag. Other walk/read/tag-validation failures or changed files make the scan incomplete;
export is refused. Empty inventories are refused to prevent accidental index
replacement. Limit: 32 MiB JSON, 100,000 unique tracks. This is a sequential scan,
not an atomic filesystem snapshot; rescan if the library is being reorganized.
No automatic jobs, fallback root search, retries or silent skipping of failures.

## Private import (VPS only, separate release authorization)

No upload API is added. After review, migration and separately authorized private
transfer, an operator can run on the VPS:

```sh
python backend/manage.py import_mac_library "$PRIVATE_SNAPSHOT_JSON"
python backend/manage.py import_mac_library "$PRIVATE_SNAPSHOT_JSON" --apply
```

The default previews aggregate create/update/missing counts without writes.
Do not run this management command on the Mac: `RUNTIME.md` remains binding.
The complete file is strictly validated before writes, including version, exact
fields, bounded strings and finite measurements, unique IDs, timestamp and size.
Duplicate JSON keys, partial/empty files, unknown fields and future timestamps
are refused. Validation errors never interpolate private data.

Apply uses one transaction and a per-collection receipt row lock. Exact replay
(timestamp + payload digest) is a no-op, including record timestamps. Older or
same-time conflicting payloads are rejected. A failed import rolls back the
receipt and all row changes; the same complete file can then be tried explicitly.
Concurrent applies serialize on the collection receipt. Preview is advisory and
can change before apply; apply checks again under its lock.

Rows use source `mac_snapshot` and an internal opaque `mac-snapshot:<uuid>:<id>`
key in the existing unique `file_path` column. This key is not a filesystem path.
Serializers return blank path/filename for these rows. Tag writes and promotion
reject them before file IO, both in services and HTTP views (409). The ready-folder
scanner cannot mark them missing. #27's matcher reads them without new matching
logic or a fabricated playback source.

A successful newer complete snapshot marks omitted records missing **only within
that Mac collection**; it does not delete rows or audio and does not modify any
ready/manual/TraxDB or other-collection records. Reappearing IDs become nonmissing.
For Mac records, missing means omitted from the latest imported scope, not a live
disk check or verified deletion. Last capture and digest remain in the private
receipt. Old observations do not expire automatically; an indexed match remains
a historical metadata observation. Keep #27's coverage qualification visible.

## Aggregate Mac dry run — 2026-11-08

Two manual read-only runs against the requested library scope returned identical
counts. No real snapshot or per-track report was saved. The second run added only
aggregate error-stage classification; exit status 2 correctly signals incomplete.

| Measurement | Count |
| --- | ---: |
| Audio candidates | 15,809 |
| Readable metadata | 15,799 |
| Artist + title tags | 9,048 |
| Measured bitrate + sample rate | 15,799 |
| Metadata-read failures | 10 |
| Directory walk failures | 0 |
| Excluded staging subtrees | 1 |
| Skipped symlinks | 0 |

Readable formats: MP3 7,710; AIFF 3,672; WAV 3,946; M4A 462; FLAC 9.
The excluded subtree was not traversed, so these are not staging/archive counts.
6,751 readable files lacked a complete tagged artist/title pair. These are
measured coverage limits, not evidence of track absence. The ten initial read failures were nine empty files and one ID3-only MP3 with
two trailing bytes. These are excluded as non-audio placeholders. The originals
are retained. The report includes no filenames, tags or exception text.

## Validation and next step

Offline fixtures use generated WAV/ID3 data and temporary roots. They cover real
Mutagen measurements, path-free stable IDs, staging/symlinks, broken/changing
files, walk failure, private no-overwrite export, size/schema failures, sanitized
CLI output and replay ordering. Run without a DB/API:

```sh
PYTHONPATH=backend python -m unittest library.test_snapshots library.test_matching -v
```

`library.test_snapshot_import` is CI-only PostgreSQL proof for preview/no writes,
replay, stale/conflict rejection, rollback/retry, collection-scoped omissions and
reappearance, matcher reuse, serializer privacy, ready-scan isolation and blocked
file operations. Dedicated `mac-library.yml` runs these plus the library migration
check; the existing full fixture workflow remains unchanged.

No real export/import, local DB/API, VPS writes, migration, merge or deployment
is part of this work. Self-review only. Rollout needs #27 integration, migration
0002 and a complete dry run before an explicit export/transfer/preview/apply.
Rollback must disable imports and remove only `mac_snapshot` index rows/receipts
before reverting the metadata-only guards; never remove user audio.

If real scan failures remain, the next contract is to diagnose them locally with
operator-controlled private diagnostics or an explicitly reviewed scope change,
then obtain an error-free aggregate dry run. Do not import the readable subset as
complete or equate unmatched/untagged tracks with verified absence. Mac playback
is separate work requiring an authorized source route; these IDs cannot be used
to open Mac files on the VPS and this slice provides no audio endpoint.

## Optional local Finder sidecar (#40)

`--local-reveal-map` explicitly opts into a separate mode-0600 local ID-to-relative-
path map for the existing sandboxed OCDJ Helper. The exported server JSON and
scan/ID policy stay unchanged. With the same root/scope, collection UUID and key,
the sidecar can be populated without another server import. See
[the local map, permission and release contract](MAC-FINDER-REVEAL-40.md).
