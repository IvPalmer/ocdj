# Beatport manifest wiring — #14 / #20

> Queue strategy superseded by [exact Beatport previews](BEATPORT-EXACT-QUEUE-14.md).

This slice is stacked on release-prep PR #24, `419dbd9`, against
`codex/14-safari-release-prep`. It supersedes only the earlier handoffs' statement
that Beatport is intentionally undeclared. It prepares source; it does not prove
installed Safari behavior or release the backend dependency.

## Source behavior

The connected `ocdj-helper/manifest.json` and the signed wrapper's
`OCDJ Helper/OCDJ Helper Extension/Resources/manifest.json` now declare exactly
`https://www.beatport.com/*` and `https://beatport.com/*`. Shared utilities load
before the adapter, with existing injected CSS at document_idle. The adapter
accepts numeric track/release routes, including two-letter language prefixes;
artist, genre, chart and other pages get no controls. Xcode already copies the
whole Resources/content folder; no project, entitlement or signing edit is needed.
Legacy `ocdj-helper-safari` / `ocdj-helper-standalone` snapshots stay unchanged.

Wanted uses existing `dig:add`: track title for tracks; blank title and populated
release_name for releases; artist and the current page URL without query/fragment
for both. It retains `source_site: beatport`. Heading and document-title agreement
is required, preserving artists and mix names. When canonical metadata exists,
its host, type and numeric ID must agree before exposing actions. Stale metadata
removes the prior controls and retries on mutation. Parentheses are normalized
only for comparison, never stripped from the payload. Missing/unrecognized
metadata deliberately produces no buttons. The title parser still expects the
site's `Artists - Title [Label] | ...` format; arbitrary delimiter changes need
fresh evidence rather than guessed metadata.

Queue uses existing `dig:queue-search` and `/api/dig/yt-search/`, retaining the
Beatport source URL/platform and the actual returned YouTube video ID. It does
not synthesize a Beatport stream or pretend a release is a specific track.
Release Queue searches artist + release name once; it does not enqueue every
release track. First-result search can select a different recording or mix.
An empty result is an error and adds nothing; API errors also add nothing.
Wanted remains dependent on #24's backend #20 change being deployed separately.
Existing release dedup compares artist/title, so different blank-title releases
by the same artist can collide. Local-only fallback is not VPS persistence.

Both options pages include Beatport in existing siteSettings, defaulting on like
the other sites. Explicit false suppresses both controls; changing the setting
removes/recreates controls without changing browser grants. Existing token,
backend settings and other per-site values retain their existing save behavior.
Other adapters' pre-existing toggle enforcement is outside this slice.

## Public source / embed verification (2026-11-08)

Read-only public Beatport track and release documents expose the heading and
artist/title format represented by the synthetic tests. Public release documents
also demonstrate language-prefixed routes. No real recording metadata or raw
page captures are committed as fixtures.

The public [Beatport embed app](https://embed.beatport.com/) currently serves an
HTML/JavaScript player. Its served client recognizes numeric `id` and `type`
values including track and release, and handles missing/unavailable items.
This corrects the old adapter comment's categorical claim that Beatport has no
embed. Client route recognition is not proof of playable output. No authenticated
Beatport API request, token extraction/use, stream extraction or audible playback
was performed. A direct public track fetch encountered a browser challenge;
no challenge bypass was attempted. Search-rendered public page evidence is not
fresh Safari DOM verification.

This slice does not integrate the native embed: the existing extension CSP does
not allow its host, and security-policy changes are excluded. Native playback,
preview duration and availability remain unverified. The supported source in the
existing OCDJ Queue mechanism is the backend-returned YouTube result. If no
result is returned, Queue has no playable source and reports an error. There is
no manufactured sample URL, guessed video ID, new host grant or CSP relaxation.

## Offline proof

Reuse an existing jsdom installation; no dependency or lockfile changes:

```sh
OCDJ_TEST_JSDOM_PACKAGE=/path/to/existing/package.json node --test tools/extension-tests/*.test.mjs
git diff --check
```

The new suite executes the declared scripts together in jsdom and verifies exact
host/resource declarations, resource existence and order, track vs release
payloads, localized routes, parentheses/mix retention, stale canonical navigation,
unsupported routes, idempotence, per-site disabling/re-enabling, options round
trips and byte-identical adapters. Xcode folder membership is checked statically.
The existing suite covers Queue results, empty-result failure/dedup, per-call
release URL/token settings, API errors and other sites' capture lifecycle.
All 40 extension tests pass (25 existing plus 15 new); 23 active JavaScript
syntax checks and diff whitespace checks pass. Workers, shared utilities, both
Bandcamp guards, existing manifest entries, host_permissions, permissions and CSP
were compared to `419dbd9` and are unchanged. Review was a single-agent
self-review, not independent review.

These are synthetic fixtures and stubbed browser/network interfaces, not a live
browser, real backend search, installed-bundle proof or independent review.
No backend source changes or backend tests in this slice; no local DB/API starts.

## Remaining live QA gates — not executed

Use the release procedure and native Safari matrix in
[SAFARI-RELEASE-14.md](SAFARI-RELEASE-14.md) only with separate release authority.

1. Review this stacked PR and #24; verify backend #20 deployment SHA separately
   from extension build SHA. No merge or deployment is authorized here.
2. After an authorized signed build/install, compare actual installed Resources
   and registration to the reviewed source. This task does neither.
3. Inspect Beatport's actual Safari host access; Ask/Deny remains blocked.
   Browser grants, profiles, auth and security settings were not changed here.
4. On operator-selected track and release pages, verify exactly one Wanted/Queue
   pair, correct placement and metadata, late rendering, internal navigation,
   Back/Forward, localized pages and the Beatport toggle. Check track/release
   transitions, same-named tracks and stale metadata. Regress the other sites.
5. Verify actual Wanted persistence, correct title vs release_name and source URL
   with authorized data access. A local-only toast or checkmark is insufficient.
6. Verify Queue source/artwork and audible intended YouTube recording/mix; release
   Queue is one search result, not full-release playback. Check empty results,
   auth errors, offline feedback and dedup. Do not substitute another recording
   as proof of exact matching. Native Beatport playback remains outside scope.

Keep #14 open for live acceptance and #20 open for its existing source/release
tracking. No profile/security changes, signing, installation, merge, deployment,
child agents or production-data access occurred. Rollback of this source slice
is its normal PR revert; no schema change or security-setting rollback is needed.
