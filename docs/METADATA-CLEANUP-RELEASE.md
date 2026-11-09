# Metadata cleanup release slice (#16)

Based on production release branch `vps-deploy` at
`4aaa283f5dfe8e11a499f9d37b0e2de2802b2ec0`. Main and release diverge;
this is not a cherry-pick of the inactive main-based stack #23/#26/#30/#34.
It reuses their immutable initial observation, conservative full-version policy,
and bounded keyless catalog transport, adapted to the actual release schema.

## Behavior and authority

Every new Wanted `save()` atomically creates a private MetadataCleanup outbox
receipt, including ordinary API POST, bulk-add, confirmed imports, Dig, Shazam
HTTP/Spotify ingestion, recognize-to-Wanted, admin, and existing Instagram
wishlist creation. Only inserts capture; updates do not replace originals.
No backfill or catalog requests for existing library/history. Legacy Wanted rows
are marked protected by migration because their provenance is unknown.

Huey dispatches after commit. Its existing consumer discovers Wanted tasks.
A one-minute bounded sweep enqueues at most 25 due receipts, recovering enqueue
failures, crashed workers and retry waits. A five-minute expiring attempt token
fences completion. There are at most three research attempts, including crashes;
transport failures wait two minutes, invalid responses stop, exhausted claims fail.
Duplicate delivery cannot apply twice. A global Huey cleanup lock serializes
cleanup catalog requests; collisions leave the durable intent due for the next
sweep. This has deliberately modest throughput and no new worker/dependency.

Missing-artist automatic application requires a producer-supplied valid ISRC,
exactly one MusicBrainz recording, the same returned ISRC, an explicit matching
recording ISRC list, and the exact full title including mix/version. Only Unicode
NFC, case and whitespace are normalized. Any catalog disambiguation blocks writes.
Nonempty artist/title values are never automatically replaced. Title-only iTunes
search (at most three songs) and mismatches/collisions remain durable proposals.
This permits clear missing-artist recovery while an incomplete/wrong recognized
value stays reviewable with the full catalog candidate, not silently overwritten.
The ISRC is recording evidence supplied by the producer, not independent proof
that fingerprint recognition itself was right. Catalog errors remain possible.

The [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API) documents ISRC
lookup as a list of recordings, artist-credits/isrcs inclusions, a meaningful
User-Agent and one request per second. Cleanup waits 1.1 seconds after its ISRC
request, under its shared Huey lock. Existing Organize MusicBrainz enrichment
is a separate caller and is not serialized by this new lock; simultaneous heavy
Organize use may still receive rate-limit responses (bounded retries apply here).
No key, netrc, environment proxy/auth, redirect, audio, pagination or URL from
provider results is followed. Each request has 5s connect/12s read timeouts,
128 KiB response limit and a 20s streaming budget checked between chunks. This
is not a hard total deadline across DNS/connect/read. Error text is never stored.

Wanted application locks the row and compares original artist, full title, ISRC,
metadata revision and protection flag, then updates and records evidence/result
in the same transaction. Application invalidates cached preview metadata. Metadata
edits through model saves increment the revision and protect the item, including
same-value/blank API confirmations. Stale whole-object background saves preserve
metadata they did not edit. Protection cannot be cleared through the public API.
This is whole-item protection; no field-specific confirmation or unprotect UI.

## Recognition entry points and identities

Wired: mix pipeline final completion (including resumed jobs), recluster endpoint,
TrackID lookup success (now saves a completed job and adds `job_id` to its existing
response), and both Shazam Wanted ingestion routes. Pipeline progress saves omit
tracklist so in-flight human corrections survive. Publishing locks the job and
preserves current manual rows; manual PATCH also locks. Manual rows survive rerun.
Recognize-to-Wanted propagates manual protection and a saved ISRC anchor.

Each accepted track row has a generated `cleanup_id` UUID. Array position is not
an identity; neither this UUID nor an ISRC claims global canonical recording
identity. Exact metadata/timestamp repeats reuse the current occurrence and its
receipt; already applied corrections survive reclustering the same raw tags.
Changed evidence/metadata after a new run may produce new occurrences. To anchor
a clustered row, all overlapping raw hits must agree on full artist/title and
one nonempty ISRC; lossy mix merges, missing IDs and conflicts cannot authorize
writes. Shazam and ACRCloud adapters now retain their ISRC field. Old saved raw
results without ISRC remain proposal-only. Recognition application compares the
entire saved track row by stable UUID under the job lock, preserving other rows.

**Not all recognition is covered.** No `backend/instagram/*` file is changed.
For the other agent/parent, the exact proposal-only integration boundary is:

```python
from wanted.services.cleanup import capture_external_identification

capture_external_identification(
    event_id=accepted_event_uuid,  # durable UUID; reuse on retry of this event
    artist=media.artist,
    title=media.title,
    evidence={"source": "caption"},  # small safe structured provenance only
)
```

Call inside the accepted-write transaction after caption acceptance in
`media_action`, accepted fenced connector `complete`, and accepted cached-worker
completion in `recognize_cached`. Generate and retain the event UUID with that
accepted event in the owning adapter; a later accepted identification gets a new
UUID. Do not pass cookies, private URLs, comments, captions, audio or raw provider
payloads. Reusing an event for changed evidence is rejected. External receipts
are protected and proposal-only; this module has no Instagram writer. Until the
parent wires that boundary, Instagram recognition is uncovered; only its ordinary
Wanted creation is centrally captured. Cratemate cover/release candidates,
Organize file-tagging and arbitrary raw SQL/bulk_create are not covered.

## Private review and audit

`GET /api/wanted/items/<id>/cleanup/` returns the original snapshot, evidence,
proposed changes, status, attempts and timestamps. Recognition detail includes
`cleanup` receipts. Django admin exposes read-only MetadataCleanup records with
no add/change/delete action. These surfaces follow existing application access
controls; this adds no public unauthenticated route or write/approval endpoint.
Ambiguous/wrong-tag proposals require a manual edit through existing metadata
controls; proposals cannot themselves grant authority to write. A result delta
is an applied change only if the receipt status is `applied`; superseded results
are hypotheses. Receipts survive subject deletion. Originals are application-level
immutable, not protected from administrator SQL. Manual edits remain in existing
subjects and revision/protection flags; this is not a general manual edit journal.

## Operations and rollback (parent only)

No merge, deployment, live API/DB, provider request or private fixture was used
for implementation. The parent owns independent review, integration, deployment
and live QA. Do not merge the inactive stack alongside this release migration.

1. Back up the production database privately. Pause backend writes and the Huey
   consumer for the schema change; deploy compatible backend/worker images from
   the same reviewed release commit. Apply `manage.py migrate` before resuming
   writes/consumer. Migration adds three Wanted fields and one receipt table;
   the legacy-protection UPDATE can lock Wanted during migration. No legacy
   metadata values change, and no backfill event is emitted.
2. Restart the existing Huey consumer so it registers the new tasks/periodic
   sweep. Confirm both services use the existing shared Huey volume. No new
   credential, service, external scheduler, local stack or auto-relaunch is needed.
3. Parent live QA: use an explicitly authorized disposable Wanted item with
   missing artist and valid exact-version ISRC from a real producer; inspect
   original/evidence/applied status and unchanged title; replay the same event;
   confirm a blank/manual edit while lookup runs is preserved. Check one mix
   result, a conflicting version proposal, Shazam ingestion and outbox recovery.
   Do not publish real listening history or audit payloads in CI/GitHub.
4. Rollback: pause the consumer, revert application code/images and retain the
   additive schema/audit table. Existing applied artist changes are not reversed
   by code rollback. Restore a specific original only after human review and a
   current-value/revision comparison; never bulk overwrite later corrections.
   Reversing migration drops audit data and protection flags and is not a routine
   rollback. Preserve a private backup first if deliberately removing the schema.

Raw SQL/QuerySet metadata writes and bulk_create bypass model capture/protection;
current production metadata API/admin paths use model save, and known bulk
creation routes loop over `.create()`. Future producers must use those primitives
or a reviewed adapter. No filesystem/audio tagging or automatic download change.

## Synthetic verification

`python tools/test_metadata_cleanup.py` runs only catalog/policy unittest fixtures
without Django setup, project settings, secrets, DB or live HTTP. Integration
uses GitHub Actions `Metadata cleanup fixture`: real PostgreSQL row locks,
full backend test discovery, Wanted/Recognize migration drift, atomic rollback,
manual confirmations and stale instance preservation, idempotence, fenced leases,
bounded retries, actual ingestion and API paths. Instagram fixture remains an
independent existing CI check. Self-review only; parent supplies review/integration.
