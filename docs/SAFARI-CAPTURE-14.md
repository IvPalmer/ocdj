# Safari capture investigation and release handoff (#14)

Observed 2026-11-08. This release port is based on `vps-deploy` at `f390d13`,
porting draft [#21](https://github.com/IvPalmer/ocdj/pull/21) at `1eb2dfa` plus
single-item Beatport source acceptance for [#20](https://github.com/IvPalmer/ocdj/issues/20).
It does not install, replace, sign, notarize, grant permissions, change
authentication, merge or deploy. Issue #14 remains open pending activation and
live acceptance below. Historical live observations below came from #21's
investigation; this port freshly verified only installed resource provenance and
registration, not live site behavior.

## Which package Safari uses

Read `origin/vps-deploy:docs/EDGE-AND-LOCAL.md` before investigating. PlugInKit's
read-only query for `com.ocdj.OCDJHelperApp.SafariExt` returned one registration:
`/Applications/OCDJ Helper.app/Contents/PlugIns/OCDJ Helper Extension.appex`.
Safari's toolbar and Extensions settings identify the enabled extension as OCDJ Dig
1.0.0 from OCDJ Helper. No stale build registration was observed.

All **22 installed resource files** byte-match the Xcode wrapper Resources tree in
`origin/vps-deploy` at `f390d13`. Six differ from `origin/main` at `fdeaa6e`:
manifest, background worker, both options files, Bandcamp and shared scripts.
The installed YouTube, SoundCloud, Spotify and Discogs scripts match both branches.
This establishes installed resource provenance, not that a newly edited checkout
has been loaded into Safari. `ocdj-helper-safari` and `ocdj-helper-standalone` are
other snapshots; neither is the registered app's resource directory.

The runbook's session-cookie explanation is stale relative to the installed
worker: that worker uses a stored Dig bearer token and reads settings per call.
Main's Safari worker defaults to localhost and lacks those release fixes. Keep
release authentication and options intact; this PR deliberately does not port or
modify them.

## Native Safari observations

Safari > Settings > Websites > OCDJ Dig showed **Allow** for api-v2.soundcloud.com,
api.discogs.com, bandcamp.com, discogs.com, ocdj.grooveops.dev, open.spotify.com,
soundcloud.com, w.soundcloud.com and youtube.com. youtube-nocookie.com was **Ask**.
Beatport is absent from the installed manifest and from that configured host list.
No settings were changed. Declared matches are distinct from Safari's grants.

| Site | Installed behavior observed | Action verification |
| --- | --- | --- |
| YouTube | Both OCDJ buttons on an existing watch page | Queue changed to a checkmark / Queued for listening; Wantlist changed to a checkmark / Saved to Wantlist |
| Spotify | Existing track page had no OCDJ buttons despite Allow | No action available; Spotify's own Queue toggle is not an OCDJ button |
| Beatport | Public numeric track route rendered heading and artist/mix metadata with no OCDJ controls | No integration in installed source |
| Bandcamp | Existing album page had one header Queue/Wantlist pair | Presence only |
| Discogs | Existing release page had header and per-track pairs | Presence only |
| SoundCloud | Public track rendered inside a nested matching `/n/<artist>/<recording>` frame; no OCDJ controls | Presence failure on the new layout; no action available |

YouTube acknowledgments alone do not prove VPS Wanted persistence, queue playback,
or that an entry was not already present. No server data or credentials were read
for this check. New code has not been installed, so all patched behavior remains
fixture-verified rather than live Safari verified.

Follow-up native inspection confirmed SoundCloud's new track layout is a nested
HTML document, rather than the legacy `.soundTitle` / `.soundActions` layout.
The installed declaration has no `all_frames`; the legacy fixture does not cover
this layout. The adapter now reads only accessible same-origin `/n/` documents
whose recording path exactly matches the outer page. It uses the frame's heading
and adjacent matching profile link for title/artist and places one button group
immediately before the frame in the outer page, retaining the declared styles and
runtime. It observes delayed metadata and replacement, preserves controls through
unrelated frame mutations, and removes stale controls after navigation/removal.
No scripts are injected into the frame and no manifest/grant changes are needed.

This is fixture-verified only. Native Safari inspection in this follow-up again
showed the installed layout without controls. Safari's isolated content-script
access to this frame, actual heading/profile DOM relationship, outer control
placement and action persistence still require the authorized release QA below.
Inaccessible or mismatched frames deliberately produce no controls; do not claim
that fixture success establishes live SoundCloud acceptance.

## Changes and workflow ownership

Empty worker replies now restore both actions for retry instead of leaving
Wantlist loading; fixtures verify a subsequent successful Wantlist/Queue action.
This retains the installed release's existing missing-response behavior in the
main source trees without changing authentication.

The shared observer retries after delayed rendering and DOM replacement, excludes
its own additions/updates, and throttles continuous host changes. Page identity
changes rebuild button closures. Injection guards compare host text/links without
our controls so reused track rows acquire fresh actions. Spotify also supports a
visible-heading anchor and prefers current visible title/artist; per-track Wanted
saves now retain the track URL. Bandcamp checks its header group's actual sibling
position before retrying. Discogs keeps its existing site script.

Beatport's prepared adapter supports numeric track/release routes, waits until
heading and document-title metadata agree, retains mix names and source URL, and
uses the existing YouTube search Queue route (first search result, not a native
Beatport player or guaranteed recording match). The Safari worker now handles that
route and stores the source and resolved video in its existing local playerQueue.
Beatport Wantlist uses `dig:add` with `source_site: beatport`; releases use
release_name. No platform spoofing or alternate backend write path is introduced.

Wantlist sends `dig:add` to `POST /api/dig/add/`; Safari also caches locally and
can fall back to local storage. The UI now labels an offline save as local rather
than implying the VPS Wanted List was updated. YouTube Queue stores a video;
SoundCloud/Spotify/Bandcamp Queue stores a native embed. Discogs Queue resolves
release videos, and its per-track route matches a track against those videos.
These queues are extension storage, not a VPS download/organize job.

## Release dependencies and port

1. **Backend:** this port adds Beatport only to `DigAddSerializer` for #20.
   Existing `add_item`, source ownership (`dig`), URL retention in notes, dedup,
   and bearer authentication stay unchanged. Beatport batch import remains
   unsupported. Track and release (blank title, populated artist/release_name)
   persistence pass focused tests. Existing validation still requires artist or
   title; release_name alone does not satisfy it. Existing dedup still compares
   artist/title, not release_name, so release-level dedup has that pre-existing
   limitation. The change is not deployed: live acceptance still requires the
   backend release. A local-only fallback is not completed capture.
2. **Beatport access:** manifests and Safari grants are unchanged under the task's
   no-permission-change boundary. The new adapter is intentionally **unwired**.
   A separately authorized release must add a content_scripts entry for
   `https://www.beatport.com/*` and `https://beatport.com/*`, loading
   `content/shared.js`, `content/beatport.js`, `styles/injected.css` at
   `document_idle`, and verify the operator's Safari access grant. Do not add
   broad host permissions or relabel a denied grant as an injection defect.
3. **Release branch:** this focused port is complete against `vps-deploy`.
   Shared.js retains release missing-response/error handling and offline feedback;
   both Bandcamp copies retain the release sibling guard. The lifecycle helpers
   and site changes are applied around those fixes. Only the Queue search handler
   is added to the Safari worker; its existing backend URL and per-call bearer
   token handling are retained. Manifest, options and signing inputs are unchanged.
   Main's entire Resources directory was not copied over the release tree.
4. Build/sign/notarize/replace the app only in a separately authorized release,
   following EDGE-AND-LOCAL.md. Recheck registration and resource hashes afterward.
   `ocdj-helper` and the Xcode Resources changes are deliberate; the legacy Safari
   and standalone snapshots are not release inputs for this registered app.
5. **SoundCloud layout:** port the frame adapter and verify the nested-document
   access, controls and metadata live before claiming #14 is fully repaired.
   The integration needs no manifest changes but remains unverified in Safari.

## Focused local checks

DOM fixtures use Node's test runner and jsdom, with no new repository dependency
or package-lock changes. The release-port run passed **25 tests** with an existing temporary jsdom
26.1.0 installation, without installing dependencies. The original 24 fixtures
failed 17 tests against the unchanged release baseline and then all passed after
porting. The additional test verifies the release worker reads current URL/token
settings on a cold call and after settings change, and propagates API errors
without adding a Queue item. If jsdom is available to this repo,
run `node --test tools/extension-tests/capture.test.mjs`. Otherwise point
`OCDJ_TEST_JSDOM_PACKAGE` to a jsdom package.json before that command.
The current task prohibits installing; reuse an existing jsdom installation.
Do not run dependency installation as part of this handoff.

All 23 JavaScript files in the two active trees pass `node --check`;
`git diff --check` passes. There is no Safari/Dig-specific CI; the existing Instagram fixture workflow
can run because this release PR changes `backend/**`. Check the release PR
status separately from these focused proofs.
The port's read-only inspection again matched all 22 installed Resources to
`origin/vps-deploy` at `f390d13`; PlugInKit points to the /Applications bundle.
No patched app was built or loaded. Native SoundCloud observations above are
from the earlier investigation.

Fixtures cover both active source trees, asynchronous injection, SPA remounts,
recycled Spotify rows, correct source payloads, Bandcamp idempotence, Discogs
regression, Beatport track/release metadata and honest offline acknowledgment.
SoundCloud frame fixtures cover canonical payloads, repeated metadata updates,
unrelated mutations, outer navigation/back, mismatched paths and frame removal.
Worker fixtures mock fetch/storage to verify Queue search, dedup and empty results.
These do not establish that today's site markup matches every selector.

## Exact remaining live QA (native Computer Use only)

After an authorized release and #20:

1. Confirm Safari still resolves the intended /Applications bundle and its
   resource hashes match the released commit. Inspect Settings > Extensions and
   Websites > OCDJ Dig without silently changing grants. Record a blocked/Ask
   grant separately; youtube-nocookie access can affect playback independently.
2. Open a YouTube watch page, SoundCloud track and set, Spotify track and album,
   Beatport track and release, Bandcamp track and album, and Discogs release.
   Choose public recordings the operator wants to save. Require one OCDJ
   Wantlist/Queue pair at each eligible header and the expected per-track pairs.
3. For the new SoundCloud layout, require the pair immediately above the nested
   track frame. Confirm heading and artist match its visible recording. Test late
   load, internal navigation and Back, and confirm unrelated waveform updates do
   not reset the buttons. If absent, record inaccessible frame versus unavailable
   metadata as unresolved; do not change grants or enable all_frames silently.
4. On each SPA, follow an internal link to a second recording, then Back/Forward.
   Scroll to lazily loaded rows. Require no duplicate groups and controls after
   delayed rendering. Click each action once and verify it identifies the second
   recording, including its artist, mix and source URL.
5. Check the extension Player queue for the added item, source link, artwork and
   playable video/embed. Beatport Queue explicitly searches YouTube: check the
   selected result is the intended mix. An empty search must show an error and
   add nothing. Repeating a queued item must not duplicate it.
6. Open OCDJ Wanted List and verify each Wantlist result's actual persisted
   artist/title or release_name and source URL. A local-only fallback, server
   validation error, auth error, or checkmark without persistence is not a pass.
7. Record results per site and route, with only sanitized public metadata in
   GitHub. Leave #14 open until activation, backend dependency and all live
   acceptance checks are complete.
