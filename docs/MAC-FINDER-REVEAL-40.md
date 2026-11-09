# Mac Finder reveal — #40, helper lane

This PR starts from production release line `vps-deploy` at `c29c114`.
It changes only the existing macOS helper, its build, and manual snapshot tooling.
The Wanted layout and backend match response belong to the parallel integration.
Nothing here starts a DB/API, accesses production, uploads an inventory, or installs
or launches an app.

## Contract

The existing helper registers exactly `ocdj-reveal`. Its open-URL delegate accepts
one URL with this exact shape:

```text
ocdj-reveal://track/<canonical-lowercase-collection-uuid>/<64-lowercase-hex-id>
```

The final component is the existing snapshot HMAC ID, not an additional token.
No query, fragment, userinfo, port, encoded characters, extra slash, path, or batch
is accepted. The browser supplies only these opaque identifiers. The helper reads
only its fixed private map; no URL can select another map or supply a root/path.
No HTTP listener, shell execution, extra app, extension message, or additional
browser/OS entitlement is introduced. Existing Safari resources are unchanged.

The local map has version 1, `collection_id`, an absolute canonical `root`, and
`tracks` mapping IDs to relative paths. It contains neither key material nor
musical tags. It is private local inventory: never commit, upload, or log it.
The helper requires a current-user-owned private regular map (no group/other
permissions or hardlinks), bounded to 32 MiB and 100,000 tracks. It rejects another
collection, unknown ID, missing/corrupt map, absolute relative paths, traversal,
excluded ID3/TraxDB components, symlinks at any filesystem component, directories,
nonregular files, and missing files. The fixed map and selected root are opened directly with macOS `O_NOFOLLOW_ANY`,
which rejects symlinks anywhere in those paths without separately opening ancestors.
Untrusted relative components beneath the selected root use descriptor-relative
`O_NOFOLLOW`; intermediate directories use `O_EVTONLY`.
Immediately before `NSWorkspace.activateFileViewerSelecting`, it verifies the
selected file is regular beneath the configured root. Failures show a local,
path-free refresh message.

The map and filesystem are not an atomic snapshot. A concurrent rename/replacement
after final validation can race Finder's path-based selection. Stop reorganizing
the library during refresh/reveal. An opaque ID is an index observation, not a
content fingerprint; no audio identity or playback guarantee is added.

## Populate/refresh locally, without server reindex

The known location, outside the repo, is:

```text
~/Library/Containers/com.ocdj.OCDJHelperApp/Data/Library/Application Support/OCDJ/reveal-map.json
```

Launch the installed helper once first so macOS initializes its sandbox container.
Then, using the **existing** root, collection UUID and key, run manually:

```sh
python tools/mac_library/snapshot.py --root "$LIBRARY_ROOT" \
  --collection-id "$COLLECTION_UUID" --key-file "$PRIVATE_KEY_FILE" \
  --local-reveal-map
```

This invokes the exact existing scan, exclusion, metadata validation and HMAC
policy, collecting relative paths only for the validated rows. A complete, nonempty
scan is required. It writes a mode-0600 temporary file and atomically replaces only
the local map. Failure preserves the previous map. The default dry run has no new
writes. No per-file paths/IDs are printed; output remains aggregate counts and
sanitized errors. One map represents one collection/root; refreshing another
collection replaces the local selection scope.

No `--export` is needed when the imported index already used identical root/scope,
relative paths, collection UUID and key. IDs stay identical, so there is no server
reindex/transfer/apply step. A renamed track gets a new ID; the old link remains
unresolvable until the server index is separately refreshed. Map generation can
also be requested alongside `--export`; publication of these two artifacts is
independent, not a cross-file transaction. Exported server JSON is unchanged and
still contains no paths.

On the first valid reveal per helper process (or after switching mapped root),
the helper asks the operator to select the **exact** mapped library root in a
native folder picker. This reuses its existing read-only user-selected-file
permission. Choosing a different folder refuses the reveal. Access lasts while
the helper runs; it stores no persistent bookmark and requests no new entitlement.
Closing the helper's last window preserves its existing termination behavior.
Parent must verify the picker and resulting sandbox access on the installed app.

## Offline verification

Use the already-required Mutagen 1.47.0, in an isolated Python environment:

```sh
python -m unittest discover -s tools/mac_library -p 'test_*.py' -v
PYTHONPATH=backend python -m unittest library.test_snapshots library.test_matching -v
xcrun swiftc 'OCDJ Helper/OCDJ Helper/FinderReveal.swift' \
  tools/mac-helper-tests/FinderRevealTests.swift -o /tmp/ocdj-finder-tests
/tmp/ocdj-finder-tests
git diff --check
```

Fixtures use generated WAV data, synthetic identities, and temporary roots only.
Python checks unchanged exported payload/IDs, shared exclusions, map-only CLI,
privacy, atomic replacement and rollback on publication failure, incomplete scans,
missing files, symlinks, path escapes and sanitized output. Swift exercises strict
URL parsing and map/target checks without launching Finder or any UI. These tests
do not prove LaunchServices dispatch, sandbox folder selection, installed Safari
selection, production click behavior, or current availability of real files.

## Release and parent integration

Wrapper and appex stay at marketing version 1.0.1; both Debug/Release build numbers
advance together to 4 (build 3 was the initial native implementation). Build with the existing Developer ID/manual hardened
runtime recipe in [SOUNDCLOUD-INLINE-14.md](SOUNDCLOUD-INLINE-14.md) and
[EDGE-AND-LOCAL.md](EDGE-AND-LOCAL.md), overriding deployment target to 26.2.
Artifacts/DerivedData must be in a separate private directory outside the repo.
Check both signatures/entitlements, notarize, staple, validate the staple, and
require Gatekeeper acceptance. No signing keys or credential values belong here.

Xcode's Release build automatically runs `RegisterWithLaunchServices` for the
isolated artifact. As already observed in #14, this can register a build copy;
this work performs no manual registration/removal, installation, or launch.
Parent must install/register the exact verified app and check Safari's selected
bundle, then populate the map with operator-controlled existing identity/key.
Parent also integrates the parallel match API/UI contract and exercises an exact
single available match through the authenticated Wanted page to Finder. Stale,
missing, duplicate and non-Mac statuses must not acquire arbitrary reveal links.
Issue #40 remains open until that integration and installed-device QA are done.

## Build 4 sandbox regression (stacked on #42)

Starts at exact #42 head `0af8cca637068541f22bb319c208a0e04a699641`;
base branch `codex/40-mac-finder-reveal` is unchanged. The map still uses
`FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]`
plus `OCDJ/reveal-map.json`. Foundation supplies the process's user-domain location;
there is no working-directory lookup or manually prepended container path here.
No location fallback, entitlement, bookmark, or permission change is introduced.

Build 3 separately opened every ancestor starting at `/`, before the root picker
could appear. Build 4 opens the existing map directly, keeps its ownership/mode,
regular-file, hardlink and size checks, then uses the exact selected root as the
starting descriptor for no-follow relative traversal. macOS rejects combining
`O_NOFOLLOW_ANY` and `O_NOFOLLOW` with EINVAL; direct anchors use only the former.

The Swift suite launches its own synthetic child via `/usr/bin/sandbox-exec` with
an explicit denial on opening a temporary ancestor and default allowance elsewhere.
It asserts the ancestor open fails with EPERM/EACCES, then runs the exact shipped
map loader and target resolver. This fails on the #42 implementation and passes on
the fix. It models the separate-ancestor-open restriction; it does not reproduce
all installed App Sandbox grants or establish the cause of the actual live failure.
Additional fixtures reject symlinks at the anchor and above it, as well as the
existing relative traversal, map privacy, wrong-ID and nonregular/missing cases.
No GUI, Finder, private inventory, production access, or local service is used.

Path-free Logger events use subsystem `com.ocdj.OCDJHelperApp`, category
`FinderReveal`: map-open, map-metadata, map-read, map-decode, map-contract,
root-open, target-open. Open failures include numeric errno; map failures include
`result=refused`. No URL, root, relative path, collection, ID, or error description
is logged. Parent owns installation and real Chrome → picker → Finder QA.
