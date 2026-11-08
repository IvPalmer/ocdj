# Safari capture investigation and release handoff (#14)

Observed 2026-11-08. This PR changes extension source only. It does not install,
replace, sign, notarize, grant permissions, change authentication, merge or deploy.
Issue #14 remains open pending the release dependencies and live acceptance below.

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
| Beatport | Public track `/track/so-fly/31141959` rendered heading and artist/mix metadata with no OCDJ controls | No integration in installed source |
| Bandcamp | Existing album page had one header Queue/Wantlist pair | Presence only |
| Discogs | Existing release page had header and per-track pairs | Presence only |
| SoundCloud | Public `/ohxalarecords/urubu-violeta-radio-edit-2` track rendered inside a nested `/n/ohxalarecords/urubu-violeta-radio-edit-2` frame; no OCDJ controls | Presence failure on the new layout; no action available |

YouTube acknowledgments alone do not prove VPS Wanted persistence, queue playback,
or that an entry was not already present. No server data or credentials were read
for this check. New code has not been installed, so all patched behavior remains
fixture-verified rather than live Safari verified.

Follow-up native inspection confirmed SoundCloud's new track layout is a nested
HTML document, rather than the legacy `.soundTitle` / `.soundActions` layout used
by the fixtures. Both the accessibility tree and screenshot showed the track
heading and native actions in that frame, without OCDJ controls. The installed
content-script declaration has no `all_frames`, and the current adapter does not
handle `/n/` track routes. Retrying the outer document cannot repair that boundary.
The existing SoundCloud fixture passing is therefore **not** acceptance evidence
for this observed layout. A follow-up must choose and verify a frame-aware
integration under the no-permission-change constraint, or obtain separate
authorization if its solution changes the manifest/access scope. Do not simply
enable frame injection: route handling and metadata/action ownership also need
verification. No manifest or Safari setting was changed during this inspection.

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

1. **Backend:** [#20](https://github.com/IvPalmer/ocdj/issues/20) must accept
   Beatport in the Dig source validation. Both branches currently reject it.
   Until then connected Wantlist returns validation failure; Safari may save only
   locally, which the revised UI identifies. Do not call that completed capture.
2. **Beatport access:** manifests and Safari grants are unchanged under the task's
   no-permission-change boundary. The new adapter is intentionally **unwired**.
   A separately authorized release must add a content_scripts entry for
   `https://www.beatport.com/*` and `https://beatport.com/*`, loading
   `content/shared.js`, `content/beatport.js`, `styles/injected.css` at
   `document_idle`, and verify the operator's Safari access grant. Do not add
   broad host permissions or relabel a denied grant as an injection defect.
3. **Release branch:** port this extension-only commit to `vps-deploy` for the
   actual signed wrapper. Resolve shared.js by retaining its release-only missing
   response/error handling and offline feedback; retain its existing Bandcamp
   sibling guard. Apply the new lifecycle helpers and the site changes around
   those fixes. A patch-application check against the release snapshot confirmed
   conflicts in Safari shared.js and both Bandcamp copies; the other changes
   pass the application check. Keep the release manifest, backend URL, token
   handling and options.
   Do not copy main's entire resource directory over the release tree.
4. Build/sign/notarize/replace the app only in a separately authorized release,
   following EDGE-AND-LOCAL.md. Recheck registration and resource hashes afterward.
   `ocdj-helper` and the Xcode Resources changes are deliberate; the legacy Safari
   and standalone snapshots are not release inputs for this registered app.
5. **SoundCloud layout:** resolve the nested-document integration described above
   before claiming #14 is fully repaired. The existing DOM retry patch alone does
   not provide controls inside the observed new track layout.

## Focused local checks

DOM fixtures use Node's test runner and jsdom, with no new repository dependency
or package-lock changes. The latest run passed **20 tests** with jsdom 26.1.0 in
an isolated temporary directory (installed with `--ignore-scripts`). The earlier
18-test run reused a preinstalled package. If jsdom is available to this repo,
run `node --test tools/extension-tests/capture.test.mjs`. Otherwise point
`OCDJ_TEST_JSDOM_PACKAGE` to a jsdom package.json before that command.
For a fresh isolated test environment, without altering the repository:

```bash
OCDJ_TEST_DIR=$(mktemp -d)
npm install --prefix "$OCDJ_TEST_DIR" --no-audit --no-fund --ignore-scripts jsdom@26.1.0
OCDJ_TEST_JSDOM_PACKAGE="$OCDJ_TEST_DIR/package.json" node --test tools/extension-tests/capture.test.mjs
```

All 23 JavaScript files in the two active trees pass `node --check`;
`git diff --check` passes. There are no configured PR CI checks.
The latest read-only inspection again matched all 22 installed Resources to
`origin/vps-deploy` and native Safari reproduced the SoundCloud nested frame.

Fixtures cover both active source trees, asynchronous injection, SPA remounts,
recycled Spotify rows, correct source payloads, Bandcamp idempotence, Discogs
regression, Beatport track/release metadata and honest offline acknowledgment.
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
3. On each SPA, follow an internal link to a second recording, then Back/Forward.
   Scroll to lazily loaded rows. Require no duplicate groups and controls after
   delayed rendering. Click each action once and verify it identifies the second
   recording, including its artist, mix and source URL.
4. Check the extension Player queue for the added item, source link, artwork and
   playable video/embed. Beatport Queue explicitly searches YouTube: check the
   selected result is the intended mix. An empty search must show an error and
   add nothing. Repeating a queued item must not duplicate it.
5. Open OCDJ Wanted List and verify each Wantlist result's actual persisted
   artist/title or release_name and source URL. A local-only fallback, server
   validation error, auth error, or checkmark without persistence is not a pass.
6. Record results per site and route, with only sanitized public metadata in
   GitHub. Leave #14 open until activation, backend dependency and all live
   acceptance checks are complete.
