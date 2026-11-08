# Wanted cleanup intake (#16)

Inactive receipt capture with scoped creation producers; no research task, dispatcher
or metadata application hook is installed.
This follows the #17 design at `63af669` and #19 contract at `49a92d9`.

## Smallest independent step

Persist an explicit intake receipt using a caller-supplied UUID event key and the
existing Wanted PK as subject identity. A Wanted row can describe a release: its
PK is neither a canonical recording ID nor evidence of recording granularity.
The snapshot preserves exact artist/title, release/catalog/label and source ID.
Granularity and field provenance remain `unknown`; metadata/protection revisions
are null. No names, timestamps or list indexes are promoted to recording IDs or
revision tokens. No new dependency is needed.

A pure evaluator still needs unresolved evidence/protection contracts. Adding
revision tracking to every writer would expand this slice into other owners'
work. Explicit durable capture is independently useful without either change.

## Implementation plan

1. Add synthetic PostgreSQL tests for duplicate and concurrent delivery, replay
   after a correction, capture versus a concurrent edit, transaction rollback,
   missing subject, key reuse across subjects and deletion retention. First run
   them red in CI; local database/API startup is forbidden by RUNTIME.md.
2. Add `WantedCleanupIntent` and an additive migration. `event_id` is globally
   unique. Store a private snapshot and creation time; there is no runnable state.
3. Add `capture_wanted_cleanup_intent(wanted_id, event_id)` in Wanted services.
   Require a UUID before DB work. Lock the Wanted row for coherent capture;
   duplicate keys return the original receipt without changing its snapshot.
   Cross-subject reuse raises an explicit conflict. Capture participates in the
   caller's transaction, so an outer rollback removes the receipt too.
4. Add an isolated PostgreSQL CI job; test migrations and the complete backend
   suite (browser fixtures retain their existing separate job). Review the diff,
   open a PR linked to #16, and record exact verification and remaining gates.

## Safety and remaining contract

Only explicit callers persist receipts. The scoped producers below capture creation. Capture
never saves/updates a Wanted row, infers human confirmation from `identified_via`,
selects a candidate, calls a provider, sends a task or writes files. Concurrent
edits serialize with capture's PostgreSQL row lock and are retained; replay never
replaces the original receipt with newer metadata. This is snapshot consistency,
not ongoing stale-proposal protection: edit-then-revert cannot be detected here.
The service does not update receipt content and no API/admin exposes it; this is
not database-enforced append-only storage against arbitrary SQL/ORM updates.
Deleting Wanted retains the snapshot and clears its live FK; event replay still
returns the same receipt, never recreating a deleted Wanted row.

The event UUID must be retained across retries by a future producer. It is an
intake-delivery identity, not a recording, occurrence or metadata revision. There
is no universal creation coverage, dispatch/recovery mechanism, evidence research,
proposal evaluator or apply path. Future integration must define remaining producer
coverage, stable recording/occurrence links, explicit selected-candidate identity,
source granularity, monotonic metadata/protection revisions (including ABA edits),
field-level human provenance and database-enforced history/atomic apply receipts.
Only after those gates can captured observations inform reviewed proposals;
these receipts alone never authorize metadata application.

All examples/tests are synthetic. Merge, migration rollout and deployment require
separate authorization. No production data is accessed by this slice.

## Ordinary creation producers (stacked on #23)

`create_wanted_with_cleanup_intent(**fields)` inserts one Wanted item and calls
existing capture with a fresh UUID inside the same transaction. The receipt
retains that key in the same commit. Capture failure rolls back both writes;
an outer rollback removes both too. Existing capture still accepts retained
keys for replay. This adds no HTTP/import idempotency: repeating a successful
create request can still create another item/receipt, exactly as before. Event
keys are never derived from metadata, list position or release identity.

Explicit callers avoid global model/signal behavior in excluded recognition
flows. Successful response shapes, statuses, dedup and summaries are unchanged.
Bulk endpoints still loop over actual insertions, each with its own atomic pair.
A late Wanted bulk-add or Dig batch failure still propagates and leaves earlier
committed pairs. Import confirmation still catches each item failure and counts
only successful pairs. Source/import rows remain outside the helper transaction.
No raw Wanted `bulk_create` caller exists in the inspected source.

### Exact route coverage

| Route / writer | Receipt coverage |
| --- | --- |
| `POST /api/wanted/items/` through serializer creation | Each insertion, including blank artist and release-only input |
| `POST /api/wanted/items/bulk_add/` | Each insertion, even identical metadata; source ID retained |
| `POST /api/wanted/import/operations/<pk>/confirm/` | Each successful selected row; invalid indices/rejected operation state create none |
| `POST /api/dig/add/` (backend only) | New insertion only; duplicate response creates none |
| `POST /api/dig/batch/` (backend only) | Each insertion; skipped duplicates create none; allowed duplicates get distinct receipts |
| Wanted PATCH/PUT, bulk status updates, pipeline/status saves | No capture; original snapshots survive manual edits unchanged |
| Admin add; direct ORM create/save/get_or_create/update_or_create; raw bulk_create; fixtures/raw SQL; historical migration models | Uncovered; no global hooks, manager override or backfill |
| Instagram wishlist / accepted recognition and recognize-to-Wanted | Uncovered and unchanged; require separately reviewed producer boundaries |
| Import trigger/preview/provider fetchers | No Wanted insertion at this stage; capture only at confirmation |

Extension files are unchanged. No provider, worker, recurring job, candidate
selection or metadata application is added. Granularity/provenance stay unknown
and revisions null; release-only input never implies a recording identity.

### Next step: research suggestions

Define an explicitly invoked, bounded, review-only suggestion contract keyed to
the retained Wanted subject/receipt IDs. Establish source evidence and exact
recording/version identity, or retain release/recording ambiguity. Preserve raw
import evidence in a separately reviewed contract: this schema retains metadata
and source ID, not provider payloads or selected track positions. Start with
synthetic missing-artist, exact-remix, release-only, conflicting-candidate and
confirmed-blank fixtures. Preserve original observations/evidence and expose
uncertainty. Unknown provenance/null revisions block automatic writes; monotonic
metadata/protection revisions and field protection across all writers remain
prerequisites for guarded application. Do not dispatch/drain these receipts as
an activation shortcut. Admin/internal coverage and recognition handoffs remain
separate work.

This producer slice has no schema change. Revert its helper/callers to stop new
creation receipts while retaining the #23 table and existing receipts. No local
DB/API or production validation; synthetic tests use disposable PostgreSQL CI.
Merge, migration rollout and deployment remain separate.
