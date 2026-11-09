# Mac Instagram poster capture (#13)

Connector slice paired with [backend/grid PR #36](https://github.com/IvPalmer/ocdj/pull/36),
using its `docs/instagram-thumbnails-contract.md`, serializers and views at
`991da77`. Target: `vps-deploy`. This document describes implementation and
synthetic validation; it does not authorize a live run, merge or release.

The parent reported checking three real posts: each exposed image versions and
a video poster. This slice uses synthetic versions of that field shape; it does
not inspect or archive real post data.

The existing explicit connector command stays the entry point. Metadata sync
still sends exactly the existing snapshot and preserves saved order and every
child. An optional process-local map keeps one poster URL per post, selecting
the smallest candidate with positive dimensions and at most four million pixels
from the parent's `image_versions2.candidates`, then the first carousel child
with qualifying image versions. Video children can supply these posters;
`video_versions` is never used for thumbnails. Missing candidates remain missing.
No generated covers, source refresh calls, video downloads or ffmpeg invocation
are introduced by thumbnail capture.

Capture activates only when the sync response supplies a nonempty `sync_token`
and missing (`available: false`) `thumbnail_posts`. Already available posts and
posts without a local candidate cause no image fetch. A deployed API without
these fields retains its prior metadata/job behavior and receives no thumbnail
requests. The synthetic CLI fixture path has no upstream poster URLs and also
does no thumbnail capture.

Each idle job poll processes at most one image, after heartbeat and claim.
Pending user jobs take priority. The existing ten-job cap is preserved. Thumbnail
work gets a five-minute budget starting at sync; on exhaustion it flushes
successful pending JPEGs and leaves unfinished posts for another explicit run.
It does not keep waiting after capture unless the operator's existing
`--work-seconds` setting requests that wait. No scheduler or background service
is installed.

Bounds and privacy:

- The existing CDN hostname allowlist and global-address DNS checks are reused.
  Byte requests use a fresh cookie-free Requests session with redirects disabled;
  Instagram cookies and signed URLs never enter API payloads or diagnostics.
- Only JPEG/PNG HTTP content is accepted, with identity content encoding, a
  2 MiB source byte cap (declared and streamed), and a ten-second elapsed budget.
  Connect/read timeouts are three seconds each. Streaming checks the elapsed
  budget between available reads, including trickling responses; old urllib3
  uses single-byte iteration. Elapsed checks also surround conversion. DNS and
  native image decode are not asynchronously preempted by these checks.
- Pillow validates JPEG/PNG format, at most four million actual pixels and one
  frame before decoding/resizing. A fresh RGB image discards source metadata;
  quality-75 output is at most 320 pixels per side and 128 KiB. Oversize,
  malformed, animated, unsupported or timed-out sources remain unavailable.
- Temporary source files live inside a private temporary directory and are
  deleted on success and failure. JPEG batches exist only in memory. Pillow is
  already a backend dependency (`Pillow>=10.0.0`); the connector requirements now
  declare that same dependency. No new image library or binary is introduced.
- Uploads use bearer-authenticated `thumbnails/`, the current sync token and
  `{post_id, jpeg}` only, at most 16 items. The worst-size batch fits below the
  backend's 3 MiB JSON cap. Thumbnail uploads use a ten-second Requests timeout.

Each source has one attempt in this run. Ordinary per-image failures do not
prevent other images uploading. Partial `retry`/`inactive` results are left for
a later explicit sync; available entries remain server-owned. A 409 discards the
pending batch and stops further thumbnail capture/upload for this run; it does
not resync, refresh the token or interfere with selected-media leases. An API
failure/ambiguous upload aborts without resending. Upstream redirects,
restriction/session/rate-limit responses stop the run and publish the sanitized
connector status, without another source request.

Validation (no local API or database):

```sh
python3 -m unittest tools.instagram.test_connector -v
python3 -m py_compile tools/instagram/connector.py tools/instagram/test_connector.py
git diff --check
```

The 28 synthetic tests include the existing ten connector/recognition tests plus
parent/child poster selection, real JPEG encoding and metadata stripping,
malformed/video/pixel/byte/deadline rejection, transient-file cleanup, warm and
legacy contracts, 16-item/full-size batches, partial failures, actual API 409
mapping, elapsed run budget, job priority and heartbeat/claim interleaving.
HTTP and DNS are mocked; no real source, cookie store, Shazam service, library,
API or database is accessed. Review is self-review only.

Release still needs #36's additive migration, backend/grid integration and
separately authorized ingress/end-to-end validation. Existing connector bearer
and browser ingress boundaries remain required. This branch changes no backend,
grid, database or deployment configuration. Connector rollback restores the
previous script and requirements; server-owned thumbnails can expire normally.
