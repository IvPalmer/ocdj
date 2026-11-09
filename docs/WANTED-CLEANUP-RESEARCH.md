# Explicit Wanted cleanup research (#16)

## Design and bounded plan

Stacked on #26. Implement one pure `build_cleanup_proposal(receipt, evidence)`
service and one JSON management command, then verify synthetic fixtures in CI.
No subject/receipt writes, queue/task hooks, schema changes, new dependencies,
provider requests or credential lookup. Instagram and library are outside scope.

Reuse Wanted's `parse_video_title` over explicitly supplied source observations.
Organize's agent enrichment requires an authenticated model process; Crate-Mate's
collectors perform live searches and can return release-level metadata. Neither
fits this offline slice. A new matcher or provider is unnecessary.

This is **offline source-title research**, not a recording identity resolver.
An operator supplies a saved receipt envelope and source observations; the command
does not read the database, export receipts or fetch URLs. Untrusted source content
is parsed as text, never executed. A recording granularity declaration is an input
claim, not verified recording identity or a human approval.

## Input

Receipt envelope: `event_id` (UUID), `snapshot` (the exact schema-v1 JSON retained
by #26). Requires a positive integer Wanted subject ID, string metadata fields
`artist`, `title`, `release_name`, `catalog_number`, `label`, source ID (integer or
null), granularity (`unknown`, `release`, `recording`), artist/title provenance
strings, and metadata/protection revisions (nonnegative integer or null). Unknown
extra JSON fields are preserved. No attempt is made to reconstruct discarded
import payloads or infer human confirmation from import selection.

Evidence: a JSON list of at most 20 observations. Each contains:

- `source_url`: HTTP(S) URL without embedded credentials; retained, never fetched.
- `raw_title`: source text, at most 2,000 characters.
- `granularity`: `recording`, `release` or `unknown`.

Optional JSON fields (e.g. provider payload, source/track position, observation time)
are retained verbatim as supplied evidence, not independently verified provenance.
Keep files/results private: they may contain source text or identifiers. No secrets
belong in these inputs or GitHub. Invalid envelopes fail closed with an error;
empty evidence and unusable observations produce reviewable uncertainty.

## Decision and review contract

Output schema v1 preserves deep copies of the receipt and evidence, identifies
the policy version and reused parser, and lists every candidate with its evidence index and
rejection reasons. `changes` records exact before/after and supporting indices.
Repeated identical input yields identical output; no timestamps/random IDs are
invented. The event UUID identifies the original intake, never a recording or a
new proposal revision.

A receipt explicitly marked as a release cannot receive recording suggestions.
Only a blank artist can currently receive a suggestion: all supplied observations
must declare recording granularity, parse losslessly to a complete artist/title,
and agree with the receipt's entire nonblank title (including version text).
Comparison ignores only outer whitespace and case. Distinct candidate artists
or titles block the whole proposal; repeated evidence does not outvote conflict.
Existing nonblank artist must agree and is never rewritten. Release/unknown source
observations block suggestions even alongside a usable observation. A blank title
has no track anchor: retain the candidate for inspection, suggest no change.
Only explicit `unknown` provenance permits a blank-field suggestion; manual,
verified, human-confirmed, absent or unfamiliar provenance blocks it. This is
conservative filtering, not a new durable protection schema.

The reused parser strips some mix labels and presentation text. If its output
cannot reconstruct the source text exactly as `artist - title` (apart from outer
whitespace), mark `parser_changed_source_text` and withhold suggestions. Thus
`Night Signal (Lena Remix)` survives, but stripped `(Original Mix)` cannot silently
be treated as the unqualified recording. This deliberately rejects some valid
alternate separators, track numbers and official-audio suffixes.

`status` is `proposed`, `uncertain`, or `unchanged`. Every result has
`applicable: false` and application blockers: review-only policy, unverified
recording identity, and unknown provenance/revisions when present. A `proposed`
artist is an inspectable hypothesis, not a reliable-match or apply authorization.
The receipt's unknown subject granularity is retained and never promoted.

For later reviewed corrections, retain this complete output as the input artifact
for a separately reviewed workflow. No accept/apply endpoint exists here. Such a
workflow still needs durable proposal/evidence IDs, selected recording/version
identity, current metadata/protection revisions (including edit-and-revert), live
subject existence checks, field-level human protection, approval attribution and
atomic change history. Editing the proposal JSON does not authorize a write.

## Invocation and validation

On an appropriately configured checkout/runtime (never boot a local DB/API):

```sh
python backend/manage.py research_wanted_cleanup \
  --receipt-json /private/receipt.json --evidence-json /private/evidence.json
```

Outputs JSON to stdout; redirect it explicitly to retain a private review artifact.
The command handler skips system checks, uses no models and writes no files. Django's
normal settings/app initialization still occurs; this is not a standalone exporter.
No background invocation, provider error retry, live-data experiment or deployment
is part of this slice.

Synthetic `SimpleTestCase` fixtures prohibit DB queries and cover missing artist,
exact remix, lossy original mix, release/unknown evidence, conflicts/duplicates,
protected blank, nonempty corrections, missing title, invalid input, determinism,
input/output isolation and command output. Full backend discovery and migration
checks run only against disposable PostgreSQL in GitHub CI under RUNTIME.md.

Rollback: remove the command and pure service; #26 receipts and producers remain
unchanged. #16 stays open for the reviewed correction and integration gates above.

## Validation receipt

Runtime head `3705853`: [Wanted CI](https://github.com/IvPalmer/ocdj/actions/runs/42539022105)
passed full backend discovery (44 tests: 43 passed, one optional browser fixture
skipped) and Wanted migration drift checks. The 12 new tests use SimpleTestCase;
all database-backed suites ran only in disposable PostgreSQL CI.
[Separate fixture CI](https://github.com/IvPalmer/ocdj/actions/runs/42539022176)
passed the frontend build, five connector tests and 15 Instagram tests including
the browser fixture. No Instagram source changed.

Test-first runs exposed absent service/command at `5921637` and the known-release
subject gap at `77aa619` before their respective implementations. Source AST and
stacked whitespace checks passed. Self-review only; no independent reviewer,
local DB/API, live/provider validation, production access, merge or deployment.
