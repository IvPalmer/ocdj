# Exact Beatport Queue — #14 / #20

Release base: `vps-deploy` at `4aaa283f5dfe8e11a499f9d37b0e2de2802b2ec0`.
This supersedes the search-based Queue strategy in `BEATPORT-CAPTURE-14.md`.
It prepares extension source only. Parent owns review, integration, signing,
installation, deployment and live Safari/audible QA.

## Behavior and scope

Both active variants use existing `dig:queue-embed` and `playerQueue` storage.
Beatport Queue supplies an exact numeric track/release source. Workers validate
HTTPS, the exact Beatport host, route/type and positive ID, then derive
`https://embed.beatport.com/?id=ID&type=track|release` themselves. A caller's
arbitrary embed URL or track metadata cannot replace this identity. Track mix
names, artists and original source URL survive capture. Canonical/heading checks,
site toggles, button placement and the Wanted capture payload remain intact.
A stale SPA Queue callback fails before observer refresh. Legacy Beatport
`dig:queue-search` calls now fail explicitly; other sites still use existing search.

The official track embed previews that ID. The official release embed offers its
own individual exact catalog tracks for selection. A release name is never used
to pick a recording from YouTube. Both panels load Beatport directly; the connected
variant bypasses its generic backend embed proxy for this platform only. Each CSP
adds exactly `https://embed.beatport.com` to `frame-src`. No host permission,
API permission, ingress or backend changes are needed.

Only Beatport gets preview-specific player dimensions and a source/availability
note. The connected proxy transport is disabled for Beatport; use the official
player controls. Existing sites retain their player paths and dimensions.
Old persisted Beatport entries are rebound in memory from their exact source URL,
with prior YouTube results removed; normal subsequent saves persist that normalized
entry. Invalid/missing sources display unavailable instead of playing a search
result. A fresh exact requeue is not blocked by a legacy search entry.

Safari's existing player resource had no popup entry point. A Queue link now opens
it in a tab from the existing footer. That panel refreshes through storage-change
events, replacing its ineffective reconnect loop to an unhandled worker port.
This applies to all queue sources without changing their adapters. Queueing stores
an item; playback/track choice happens in the player, with no claimed autoplay.

A reproduced pre-existing bug deleted two queue entries when one was removed:
the panel saved its splice, then the worker removed the same index again. Both
panels now save once. Real worker/panel synthetic fixtures verify the neighbor
survives. There are no backend or shared app-player edits.

## Public source evidence

Read-only inspection on 2026-11-08 of the official
[embed app](https://embed.beatport.com/) and its referenced public client
`/assets/index-5NepG0OK.js` established:

- Its `id` and `type` parameters select track/release, among other types.
- The release flow requests its catalog tracks. Its rendered `TrackList` selects
  the clicked track as the player's current track and loads/plays that recording.
- Preview models read supplied `sample_url`, `preview.mp3.url`, or
  `samples.mp3[0].url`; sample offsets are supplied metadata. We neither guess
  these URLs nor extract/cache media or tokens. Only the official embedded app
  obtains its own catalog/playback data.
- Its public UI has missing/unavailable/error states. These are preferable to a
  fabricated alternative recording. Our persistent note links to the exact source
  if the iframe cannot load. Cross-origin iframe errors cannot be reliably
  inspected by the extension, so frame load is never asserted as audible success.

No authenticated Beatport request, real account operation, challenge bypass,
private media capture or audible playback was performed. Public client behavior
is evidence of supported routing/UI, not proof of current playable availability.
No raw page/client downloads are included in the PR or fixtures.

## Authentication finding

A simple token-less Safari session restoration is not supported by the release
source. `backend/dig/auth.py:require_dig_token` guards `add_item` and explicitly
rejects a missing/invalid bearer; it has no authenticated-session alternate path.
The Safari worker already uses `credentials: include` plus per-call token reads.
Changing credentials mode alone cannot satisfy the backend guard. The connected
worker's cookie-only API behavior is unchanged. Exact Beatport queueing uses no
OCDJ API calls; Wanted sync still depends on the parent's existing DIG token setup
in Safari options. No token/config value was read or changed for these tests.

## Proof and integration

Run with an isolated existing jsdom installation:

```sh
OCDJ_TEST_JSDOM_PACKAGE=/path/to/package.json node --test tools/extension-tests/*.test.mjs
find ocdj-helper 'OCDJ Helper/OCDJ Helper Extension/Resources' -name '*.js' -print0 | xargs -0 -n1 node --check
git diff --check
```

The added GitHub Actions workflow runs the same fixtures and syntax checks with
Node 22 and isolated jsdom 26.1.0. No runtime dependency or lockfile changes.
Fixtures cover the declared content script → real message router → persisted
queue → panel, exact IDs/mixes, release selection URL, malformed source rejection,
no-network queueing, deduplication, stale SPA callbacks, storage failures, legacy
queues, Wanted release semantics, popup access, storage refresh, single-entry
removal and existing sites' capture/search/auth regressions.

These are synthetic DOM/browser/storage/network interfaces. GitHub Actions is the
integration gate; its actual run result belongs in the PR/handoff. Self-review is
not independent review. No local Django/DB/API was started.

## Parent operations and rollback

1. Review this PR against `vps-deploy`, retain #14/#20's live acceptance tracking,
   and combine with indexed playback before staging a helper artifact.
2. Reserve the next version/build with the parent. Native Finder build 4 sources,
   Xcode project, entitlements, URL handling and all native code are untouched.
   No build number was consumed and no artifact was built/signed/installed here.
3. Use the existing signing/notarization/stapling pipeline after integration.
   Preserve the previous build 4 artifact/install for rollback and verify both
   native Finder reveal and indexed playback in the combined helper.
4. Verify Safari loads that exact signed resource set. With existing host access,
   test one visible pair, same-title/mix transitions, Back/Forward, late metadata,
   Beatport toggle and the existing sites. Use the popup Queue link, choose an
   exact release track and confirm audible intended recording, preview limits,
   transport behavior and queue persistence/removal.
5. Test unavailable/region-restricted/offline preview behavior: official error or
   source link, never another recording. Verify actual Wanted persistence after
   the parent sets the existing token. A local-only toast is not persistence.

Source rollback is a PR revert and rebuild using the retained release pipeline;
installed rollback restores the retained signed build 4 artifact. No schema or
server config rollback is involved. Normalized browser queue entries have exact
embed URLs and no generic videos; an old helper may not support that Beatport
embed/CSP, so remove/requeue those entries if rolling back. Do not restore a
wrong YouTube result as supposed exact playback. Existing Wanted data is untouched.
