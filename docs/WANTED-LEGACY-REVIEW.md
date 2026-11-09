# Historical Wanted review (#16)

This is an explicit one-off proposal intake for rows protected by migration 0010.
It never saves Wanted metadata, clears protection, or applies candidate credits.
No schema change. Parent owns review, release and the actual batch. The operator's
reported 307-row cohort and incorrect import credits were not queried here.

## Selection and bounds

`review_legacy_wanted` requires a frozen `--through-id` determined privately by the
operator for the historical cohort. It selects protected rows in ascending PK
order, after `--after-id` and at or below that bound, excluding any row with its
ordinary `wanted:<pk>` capture receipt. New ordinary saves atomically create that
receipt, including protected new rows, and are never recaptured here. Protection
alone is not treated as proof of historical origin. Raw SQL/bulk inserts bypass
ordinary capture: the frozen historical boundary is essential; do not use a moving
"current maximum" each batch. Unknown provenance never grants write authority.

The default limit is 25, hard maximum 100. Empty artist/title is not a filter.
Previously owner-edited historical rows can still be researched, but retain all
values and protection. Each selected row is rechecked under its row lock; an
immutable `wanted-legacy-review:v1:<pk>` receipt is created once. Repeating the
same window reuses the receipt, without refreshing snapshots or retry budgets.
Use the reported `next_after_id` to advance the window; already captured rows
still occupy their window so replay is deterministic. A row changed or removed
between selection and capture may be skipped. No continuous/new-row backfill.

## Parent production steps (not executed here)

After independent review, green CI and separately authorized deployment of matching
backend/worker code, preserve a private database backup. Confirm migration 0010 is
already installed, existing Huey consumer/outbox is healthy, and freeze the
historical upper PK privately. Run in the existing VPS backend container, never on
a local Django/DB. Substitute the private historical bound and cursor below:

```sh
python manage.py review_legacy_wanted --through-id HISTORICAL_MAX --limit 25 --dry-run
python manage.py review_legacy_wanted --through-id HISTORICAL_MAX --limit 25 --enqueue
python manage.py review_legacy_wanted --through-id HISTORICAL_MAX --limit 25 --report
```

Start with a small explicit limit (e.g. 5), inspect receipts privately, then advance
`--after-id CURSOR` for each approved window. Preview and report never call a
provider, dispatch a job or write receipts. Enqueue writes only receipts and uses
the existing after-commit Huey dispatch, shared `metadata-cleanup-catalog` lock,
outbox recovery, cooldowns, fenced leases and maximum three attempts. No new
scheduler, service, dependency or direct synchronous provider path. Existing
consumer has modest throughput: allow time before reporting. Report mode prints
only aggregate statuses/candidate assessments/hypothesis counts and PK boundaries,
not artist/title/source/notes/evidence payloads. Report windows use durable receipts,
including deleted or subsequently unprotected subjects. Keep reports private.

Without an ISRC, review queries the complete original title alone, avoiding a bad
label/compilation artist contaminating the search. It reuses the bounded iTunes
collector (at most three results, existing cooldown/transport restrictions). With
an ISRC it reuses MusicBrainz. No notes, source URLs or private account data are
sent. Provider coverage may be sparse and title-only results can describe other
recordings. Full-title equality is labeled **identity unverified**; a differing
remix/version/title is labeled **version or title conflict**, never a verified
correction. No fuzzy title or remix stripping. Strict anchored hypotheses use
existing policy, stored under `proposed_changes`; `changes` is always empty.
Candidate artist/title/provider identity remain in the private evidence receipt.
This researches review leads; it cannot reliably infer the right track artist
from every historical compilation/import title or validate publisher tracklists.

`GET /api/wanted/items/<id>/cleanup/` falls back to the historical receipt when
there is no ordinary receipt. Existing MetadataCleanup admin shows it read-only.
Receipts retain original snapshot, evidence, assessments and hypotheses, surviving
subject deletion. An edit, revision/protection change or deletion during research
marks the result superseded. No result status from this lane claims application.
Retries reuse the original snapshot, and even later unprotection cannot convert
this receipt to an automatic application. Do not reset/relabel receipts to "apply"
anything. Any correction needs a separate human-reviewed existing edit flow,
checking the current values/version; never bulk unprotect or overwrite history.

Rollback: stop issuing batches. To halt already queued research, parent may pause
the existing consumer while reviewing impact on other jobs; retain receipts and
schema. Code rollback alone does not cancel pending outbox work: the older worker
still researches queued receipts and classifies protected subjects as superseded.
Keep historical subjects protected. There are no metadata changes to undo from
this lane. Do not delete audit evidence as a rollback shortcut.

## Verification

Pure synthetic policy/catalog fixtures run without Django setup/DB/HTTP. Actions
runs PostgreSQL-only integration proofs for bounded selection, preview, idempotent
capture, API/report visibility, complete Wanted field preservation, stale/deleted
subjects, independent key fencing, concurrent capture and a real owner edit while
lookup runs. Existing shared-worker fixtures cover retries, leases and outbox
bounds. No production data, live catalogs, secrets or local Django/DB are used.
These are self-review fixtures; the parent supplies independent review and the
actual small-batch private outcome assessment before proceeding.
