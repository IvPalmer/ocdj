# Instagram recognition and playback contract (#13)

First slice: full native Post/Media collection, selected private source transport,
caption/comment evidence and source-audio recognition. No release activation.

## Existing durable identities and accepted writes

- `Post.pk` and `Media.pk` are stable native occurrence IDs; Instagram IDs are
  connector-only upstream identities. Sync updates rank/position, never recreates
  an existing occurrence or resets review, corrections, candidates or Wanted links.
- `POST /api/instagram/media/{id}/recognize/` first parses explicit caption IDs:
  `ID: Artist - Title`, `Track: Artist - Title`, or numbered carousel track lists.
  Mix/version text is retained. This is post/position evidence, never proof that
  every second of a video contains the recording. Other captions fall through.
- Cached sources dispatch through existing Huey to bounded ffmpeg/Shazam sampling
  at 0/30/60 seconds. Uncached sources need the bounded Mac connector. Playback
  and recognition use separate request flags and a shared fenced lease/action.
- Connector reads at most one comment page (20 top-level comments), before Shazam.
  Explicit comment IDs are tentative suggestions. Partial comment access is never
  represented as a complete search. Auth/restriction/rate-limit errors stop the
  run; ordinary comment unavailability permits audio fallback.
- `Media.candidates` remains `[{artist, title}]` for compatibility. `recognition`
  stores `source` (caption/comment/shazam), `certainty` (explicit/tentative),
  `outcome` (identified/no-match/error), `comments` (not-read/partial/unavailable),
  and bounded `records` (source, comment_id, text, candidate). These are private
  source evidence, not instructions or automatic enrichment authority.
- More than one candidate is never automatically selected. A single candidate
  may populate empty, unreviewed artist/title; existing metadata and corrections
  are preserved even when not yet reviewed. Select a candidate explicitly to
  replace a previous identification.
  Manual corrections fence results in flight. Wishlist reuses existing Wanted
  identity and APIs without changing previous Wanted items.
- `POST .../playback/` requests only the selected media. Private range streaming,
  64 MiB per file, 512 MiB aggregate and 24-hour TTL remain in force. JPEG/PNG
  images share the private cache and native carousel. No CDN URLs or cookies
  enter the server database. Sync downloads no live MP4s.

## Coordination with #16

Integration points are caption acceptance in `media_action`, accepted fenced
connector `complete`, and accepted cached-worker completion in `recognize_cached`.
Use those accepted-write boundaries, not browser playback or tentative HTTP work.
Wanted creation remains its own producer and must be deduplicated centrally.

This slice does **not** provide a global recording ID, durable selected-candidate
ID, metadata/protection revisions, field-level human provenance, immutable edit
history or a durable enrichment outbox. Media PK is an occurrence identity, never
a canonical recording identity; JSON array position is not an identity. #16 must
remain proposal-only until a reviewed integration slice adds these contracts to
all producers, with guarded writes and crash recovery. No cleanup hook is active.

## Follow-on slices still owned by #13

- Bounded Mac checks found readable iTunes and Rekordbox binary databases, but
  no XML export at the standard location. Neither is plain SQLite. No full scan,
  credential recovery or active TraxDB access was attempted.
- Verify complete/fresh operator-owned Mac library inventory before implementing
  present/absent/unknown matching. Backend LibraryTrack covers only ready pipeline
  files and cannot prove DJ/Music-library absence. Return unknown until verified;
  quality must come from inspected format/bitrate/sample-rate, never extension alone.
- Mount a single player above Routes. All track surfaces (including Shazam) should
  call the same play/enqueue contract; retain current item and queue across view
  changes, with previous/next/play/pause. Keep original and candidate auditions.
- Reuse Wanted actions and Soulseek search only. #15 owns Soulseek clear UI/backend;
  coordinate any shared edits before changing those files. #14 owns Safari extension.

## Release and live blockers

2026-11-08 read-only verification: VPS remains f390d13 on vps-deploy, compose
services running; unauthenticated public workspace responds 401; host-managed
routing references OAuth and has no Instagram connector bypass. Production and
main remain divergent. Reconcile deployment explicitly in a separately authorized
release; never copy this architecture to production from the source worktree.

The background Mac process sees Profile 1's cookie database but cannot open it
(PermissionError); yt-dlp consequently reports the database missing. No live
Instagram collection/comment/CDN request executed in this process. The operator's
prior complete probe (256 posts/1,232 video children/22 pages) is supplied evidence,
not repeated verification. Verify the full import, one bounded comment read and
selected cookie-free CDN transport from an already authorized Mac process before
release. No credential export, new credential transfer, LaunchAgent or scheduler
was installed. Continuous uncached private access remains dependent on a running,
authorized, explicit bounded Mac session; no always-on route is claimed.
