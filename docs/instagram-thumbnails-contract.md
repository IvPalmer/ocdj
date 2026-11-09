# Private post thumbnails — backend/grid slice of #13

Based on `vps-deploy` at `1fa67c3`. Draft only; no production migration,
data sync, ingress change or deployment. Connector implementation is a separate
ownership/integration gate: this change does not edit the Mac connector.

## Mac sync contract

The authenticated metadata `POST /api/instagram/connector/sync/` still accepts
exactly the existing snapshot, preserving saved order, post/media IDs and human
review/recognition state. Its response adds:

- `sync_token`: signed, expires after one hour and is invalidated by the next
  metadata sync. This is fencing, not authentication; bearer auth is also required.
- `thumbnail_posts`: ordered `{post_id: "123", available: false}` records for
  current active posts. `post_id` is the source post ID, not a Django PK.
  Availability checks the actual file and thumbnail expiry, independently of video.

For posts without a current thumbnail, capture **one real small JPEG per post**
during the same explicit authenticated Mac run. Source field support: the existing
parser already reads `image_versions2.candidates` for image sources; those image
versions can also contain a video poster. Prefer a bounded small candidate from
the parent post, then the first child with image versions (including a video
poster). Absence is legitimate: leave unavailable for a later explicit sync,
never generate a placeholder JPEG, fetch a video for a poster, or reorder/drop a
post. Real-source field coverage has not been verified by this backend slice.

Upstream URLs and cookies stay in the Mac process. Use the existing CDN allowlist,
public-address checks, cookie-free byte requests, redirects disabled, bounded
byte/pixel/deadline limits, and transient local storage. Convert images to JPEG
with at most 320 pixels per side and 128 KiB, discarding metadata. No eager video
downloads, recurring services, arbitrary URL proxy or source URLs in API payloads.

Send batches of at most 16 using the existing bearer route boundary:

```json
{
  "sync_token": "value from this sync response",
  "thumbnails": [{"post_id": "123", "jpeg": "base64 JPEG bytes"}]
}
```

`POST /api/instagram/connector/thumbnails/` accepts JSON only, capped at 3 MiB
before parsing; decoded JPEGs are capped at 128 KiB and four million source
pixels, decoded and re-encoded server-side with Pillow (already installed).
Unknown fields, duplicate IDs, overlong strings and overlarge batches fail with
400; oversized request bodies return 413; stale/forged sync tokens return 409.
Missing/invalid bearer returns 401; disabled bearer configuration returns 503.

A 200 response contains ordered `results` records `{post_id, status}`:
`available` (installed or already present), `retry` (invalid JPEG/capacity), or
`inactive` (not in the current snapshot). Successful items survive ordinary
partial image failures. Repeating a batch is idempotent and does not replace fresh
files. A missing file is retryable even before metadata expiry. A later explicit
sync advertises only missing/expired posts; do not automatically retry upstream
restriction/session/rate-limit failures. Stop on 409 until the next explicit sync.

## Backend/grid and retention

An additive migration adds only `Post.thumbnail_key` and `thumbnail_until`.
No existing tables/rows or review fields are replaced. Keys are random opaque
names. Files live in `thumbnails/` within the existing private `instagram_cache`
volume, outside media/static routes: 0700 directory, 0600 JPEGs, 64 MiB aggregate
cap including orphans, 30-day TTL, no new volume. A file lock serializes capacity
checks, writes/install and cleanup. Collection locking fences concurrent syncs.
Existing heartbeat/sync and Huey cleanup calls expire/inactivate metadata and
remove unreferenced owned files after the existing five-minute upload grace.
Video cleanup never expires thumbnails. Fresh entries are preserved across syncs.

Workspace JSON adds only `thumbnail_url` per post (same-origin opaque version,
or null). Grid cards use lazy async-decoding `<img>` elements, independent of
selection, video cache and Mac heartbeat. Failed images have a text fallback;
a replacement key remounts the image so a later successful capture loads.
`GET/HEAD /api/instagram/posts/PK/thumbnail/` reads only an active unexpired owned
JPEG, returns 404/410 when unavailable, and sets private/no-store (including
missing responses) and nosniff on success. It neither redirects nor fetches URLs.

## Required release gates

Browser thumbnail delivery inherits the **authenticated outer browser boundary**
of workspace/stream; Django does not itself authenticate those browser routes.
Do not exempt `/api/instagram/posts/` as a connector path. Preserve a private
origin with no direct public backend/volume exposure. Before separately authorized
release, verify outer ingress denies missing browser authentication, rejects a
connector bearer as a browser bypass, and allows the authenticated browser's
same-origin JPEGs. Existing connector exemptions must allow only connector paths
and must still require the bearer. No live boundary validation is claimed here.

CI uses synthetic PostgreSQL/browser fixtures for partial retry, stale sync,
metadata preservation/order, expiry/removal, JPEG validation, auth and grid images.
Local pure JPEG/cache tests and frontend build require no local API or DB.
Mac capture wiring and fixture proof of real source-version selection are required
before this draft can be treated as the complete thumbnail feature. Deployment
requires the additive migration first; rollback may leave the nullable columns
and bounded private cache in place without discarding old data.
