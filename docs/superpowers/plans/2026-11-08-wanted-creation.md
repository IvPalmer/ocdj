# Wanted creation receipt plan (#16, stacked on #23)

Goal: persist inactive receipts for ordinary new Wanted items without capturing
manual edits or changing successful response/status behavior.

Design decision (delegated by operator): use an explicit atomic creation helper
in the existing intake service. Route Wanted serializer creation, bulk-add,
confirmed import and backend Dig single/batch insertions through it. Keep the
existing per-item batch semantics; do not make entire batches all-or-nothing.
Global signals/model hooks would also affect excluded Instagram/recognize flows.
Raw bulk_create changes current error semantics and has no existing Wanted caller.
Admin/direct ORM creation remain uncovered, explicitly documented.

Plan (execute inline, no child agents):
- [x] Add synthetic route tests for exact snapshots, edits, duplicates, successful
  batch counts/statuses, capture failure and outer rollback; observe red in CI.
- [x] Add one helper that inserts and captures in the same transaction with a new
  event UUID, then wire the five creation routes without changing update paths.
- [x] Run full backend PostgreSQL CI and migration drift check. Review actual diff.
- [x] Document covered/uncovered routes, inactive research gates, validation and
  rollback. Open/update draft PR against codex/16-metadata-protection.

Constraints: RUNTIME.md prohibits local DB/API. No provider calls, production,
metadata apply, recurring jobs, Instagram/recognize or extension edits, merge or
deploy. No schema change. Research suggestions remain a separate reviewed slice.

Validation record:
- Tests-first `28e9a2d`: Wanted CI 42537018901 failed with the six expected
  missing-producer failures (2 assertions, 4 missing receipts); existing tests
  remained green.
- Runtime implementation `73ef11e`: Wanted CI 42537175200 passed all backend
  discovery (32 tests, 31 passed, one optional browser skip) and Wanted migration
  drift. Six route/rollback tests pass. No local DB/API was started.
- Changed Python files parsed with stdlib AST; diff whitespace check passed.
- Self-review only; no child agents or independent review. Draft PR #26 targets
  codex/16-metadata-protection. Coverage and research-suggestion next steps are in
  docs/WANTED-CLEANUP-INTAKE.md. #16 remains open; no merge/deploy/production.
