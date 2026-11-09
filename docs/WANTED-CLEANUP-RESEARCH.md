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

## Explicit catalog evidence slice (stacked on #30)

The offline mode above stays available unchanged. A second, mutually exclusive
mode gathers candidates from **iTunes**, already used by Wanted's preview service:

```sh
python backend/manage.py research_wanted_cleanup \
  --receipt-json /private/receipt.json --catalog itunes
```

This is an explicit network action: it sends only the retained artist (when
present) and full title to Apple's public search endpoint. With missing artist,
the query is title-only. No DB export or subject lookup occurs. The receipt must
validate before requesting; blank titles or queries over 512 characters produce
`lookup.status: not_queried`. It never reads configuration/credentials for a
provider. The request session disables environment auth/proxies (including netrc),
follows no redirects and makes one request without retries, pagination, fallback,
detail fetches or audio requests. The helper lives in the existing preview service;
preview selection and all existing callers are unchanged.

Bounds: three song results requested; a 12-second connect/read timeout (not a
whole-operation wall-clock deadline); at most 128 KiB of decompressed response
body; strict response/count/row validation; 700-character artist/title/URL fields.
Larger/malformed or non-song responses fail closed, without partial evidence.
Response size is enforced while streaming, including missing/incorrect length
headers. No transport exception text, URL, payload or private path is logged.

Output retains #30's receipt/evidence/candidate/reason/change/application contract
and adds `lookup` with provider, exact query, result limit, status and reason:

- `results`: every returned song retained, including its track ID, source URL,
  raw artist/title and complete bounded provider row. A preview URL is not required.
- `no_result`: a valid empty result page; not proof the catalog lacks the recording.
- `transport_failure`: timeout, connection/HTTP/redirect failure; never retried.
- `invalid_response`: unusable JSON, oversize response, invalid row/count or locator.
- `not_queried`: absent title or oversized query; no request made.

All candidates feed the **unchanged #30 evaluator** as `granularity: unknown`.
An iTunes song is a catalog track hit, not proof that the Wanted receipt identifies
that particular recording/version. Even one exact title hit cannot suggest an
artist: `source_not_recording` keeps `changes: {}` and `applicable: false`.
Same-title IDs, different artists, remix/original mix variants and duplicates all
remain inspectable without choosing a first hit or voting. Raw version text and
provider IDs survive even when #30's parser strips labels. Catalog identifiers
identify search rows, not verified matches or approvals. Results are not persisted;
stdout can be explicitly retained privately for review under #30's later gates.

The three-result window is intentionally non-exhaustive; ordering, storefront,
catalog availability and metadata quality can hide alternatives. There is no
reliable identity selector, automatic cleanup, correction writer or durable review
store in this slice. Provider output is untrusted text. No source URL is fetched.
Django management invocation still performs normal app/settings initialization;
`tools/test_wanted_cleanup.py` instead runs synthetic fixtures with minimal settings,
no dotenv/project settings, credential reads, DB setup or API. Full backend suite
and migration checks run only in disposable GitHub CI, per RUNTIME.md.

Validation covers missing artist, exact remix, same-title distinct IDs/artists,
version loss, preserved human fields/release subjects, invalid receipt/no request,
empty results versus timeout/connection errors, malformed/non-song/oversized rows,
HTTP/redirect failures, bad JSON/stream size limits and explicit command mode.
No live provider call, live user data, tag write, background activation or deployment
was used. Self-review only; no subagents. Rollback: remove catalog helper/service
and command flag; offline #30 research and preview callers continue unchanged.
