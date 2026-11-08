# Wanted cleanup intake (#16)

Inactive foundation; no producer, task, dispatcher or application hook is installed.
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

Only explicit callers persist receipts. No existing caller is changed. Capture
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
is no automatic creation coverage, dispatch/recovery mechanism, evidence research,
proposal evaluator or apply path. Future integration must define central producer
capture, stable recording/occurrence links, explicit selected-candidate identity,
source granularity, monotonic metadata/protection revisions (including ABA edits),
field-level human provenance and database-enforced history/atomic apply receipts.
Only after those gates can captured observations inform reviewed proposals;
these receipts alone never authorize metadata application.

All examples/tests are synthetic. Merge, migration rollout and deployment require
separate authorization. No production data is accessed by this slice.
