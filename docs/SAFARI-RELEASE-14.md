# Release-port review and remaining operator QA (#14 / #20)

> Follow-up: [Beatport manifest wiring](BEATPORT-CAPTURE-14.md) is stacked on
> `419dbd9` and supersedes the historical unwired/unchanged-manifest statements
> below for that slice only. Installed Safari and live QA remain separate gates.

This is source preparation for `vps-deploy`, not a shipped Safari fix. Read
[the capture handoff](SAFARI-CAPTURE-14.md) for fixtures, frame behavior and site
observations, and [the release runbook](EDGE-AND-LOCAL.md) for the existing signing
recipe. Its final cookie-auth paragraph is historical: the release worker uses
the existing Dig bearer token and reads settings on every request. Do not change
auth, token settings, grants or signing configuration to test this port.

## Review boundary and proof

- Base: `vps-deploy` at `f390d13`; source: draft #21 at `1eb2dfa` against main.
- Shared helpers are ported around release error/offline behavior; both release
  Bandcamp sibling guards are retained. The Safari worker adds only Queue search.
  Connected and signed-wrapper source differences remain deliberate.
- Beatport backend scope is one allowlist entry in `DigAddSerializer`. Existing
  view, authentication, Wanted source model and migrations are unchanged.
- Both manifests, options, native project/signing files and legacy Safari and
  standalone snapshots are unchanged. No Soulseek, Instagram or metadata paths
  are included. Beatport remains unwired in both active manifests.
- All 24 original Node/jsdom fixtures pass; a release worker URL/token/error
  regression brings the total to 25. All 23 active JavaScript files pass syntax
  checks, and diff whitespace checks pass. No new package or lockfile dependency. No Safari/Dig-specific CI is configured;
  the existing Instagram fixture workflow may run on this backend change.
- Seven backend tests pass using Django 5.2.17, DRF 3.18.2 and rapidfuzz 3.11.0
  from the existing VPS backend container. Before the allowlist change the tests
  reported four failures caused by rejected Beatport source validation.
  Tests exercise real serializers, decorated views, ORM persistence, migrations
  and track dedup; missing/wrong token is 401, unset token is 503, and invalid
  source/empty recording is 400. Test tokens and recordings are synthetic.
- Backend proof ran in a temporary extracted source directory in a separate
  Python process with in-memory SQLite and `dig.test_settings`. This loads no
  production settings/.env, starts no service/worker and accesses no production
  DB. The directory and database were removed at completion. This is not a
  PostgreSQL or live persistence test. No production source was replaced.

To rerun with already available dependencies:

```sh
OCDJ_TEST_JSDOM_PACKAGE=/path/to/existing/package.json node --test tools/extension-tests/capture.test.mjs
python backend/manage.py test dig.test_beatport --settings=dig.test_settings --verbosity=2
git diff --check
```

The isolated settings URL root is `dig.urls`, so tests call `/add/`; production
mounts the same view at `/api/dig/add/`. Tests do not replace production routing.
Existing release dedup compares artist/title, including for releases; it does not
distinguish two blank-title releases by release_name. This port preserves that
behavior. The source URL stays in Wanted notes under the existing `dig` source.

## Future signed-app steps — separate release authorization required

Nothing below was executed in this task. Record the reviewed commit SHA and
preserve a rollback copy of the currently signed application before replacement.

1. Review/merge the release PR only when authorized, then release the backend
   using the VPS runbook in RUNTIME.md. Verify the backend SHA separately from
   the app's source SHA. No signing or installation can release backend #20.
2. Decide whether Beatport activation is authorized. Without a subsequent
   reviewed manifest change it cannot inject, even after this backend release.
   Its exact `content_scripts` matches should be `https://www.beatport.com/*`
   and `https://beatport.com/*`, JS `content/shared.js`, `content/beatport.js`,
   CSS `styles/injected.css`, `run_at: document_idle`. Keep other declarations
   and grants unchanged; do not add broad hosts or `all_frames` for SoundCloud.
3. Build from the reviewed release checkout using the existing Developer ID
   identity/team and notarization credentials kept outside GitHub. Variables
   below refer to those existing values; do not create keys or change signing
   settings. The flags are the existing runbook recipe:

   ```sh
   xcodebuild -project "OCDJ Helper/OCDJ Helper.xcodeproj" \
     -scheme "OCDJ Helper" -configuration Release \
     -derivedDataPath "OCDJ Helper/build" build \
     CODE_SIGN_IDENTITY="$OCDJ_SIGN_IDENTITY" CODE_SIGN_STYLE=Manual \
     DEVELOPMENT_TEAM="$OCDJ_SIGN_TEAM" CODE_SIGN_INJECT_BASE_ENTITLEMENTS=NO \
     OTHER_CODE_SIGN_FLAGS="--timestamp --options=runtime"
   OCDJ_RELEASE_APP="$PWD/OCDJ Helper/build/Build/Products/Release/OCDJ Helper.app"
   codesign --verify --deep --strict --verbose=2 "$OCDJ_RELEASE_APP"
   codesign -d --entitlements - "$OCDJ_RELEASE_APP"
   codesign -d --entitlements - "$OCDJ_RELEASE_APP/Contents/PlugIns/OCDJ Helper Extension.appex"
   ditto -c -k --keepParent "$OCDJ_RELEASE_APP" /tmp/ocdj-helper-release.zip
   xcrun notarytool submit /tmp/ocdj-helper-release.zip \
     --key "$OCDJ_NOTARY_KEY_PATH" --key-id "$OCDJ_NOTARY_KEY_ID" \
     --issuer "$OCDJ_NOTARY_ISSUER" --wait
   xcrun stapler staple "$OCDJ_RELEASE_APP"
   xcrun stapler validate "$OCDJ_RELEASE_APP"
   spctl --assess --type execute --verbose=2 "$OCDJ_RELEASE_APP"
   ```

   Require valid Developer ID signatures, explicit app sandbox entitlements and
   no get-task-allow, successful notarization/stapling and Gatekeeper acceptance.
   Stop on failure; do not enable unsigned extensions or alter signing/auth.
4. Only after authorized replacement of `/Applications/OCDJ Helper.app` and
   launch of its container app, re-query registration:

   ```sh
   pluginkit -m -A -D -vvv -p com.apple.Safari.web-extension -i com.ocdj.OCDJHelperApp.SafariExt
   ```

   Require the /Applications bundle, not a stale DerivedData registration. Never
   use `pluginkit -r` as a workaround. Compare every installed Resources file's
   bytes and file set with the built wrapper and reviewed source; record hashes
   and SHA privately. Current installed baseline has 22 files; this port adds
   `content/beatport.js` but does not declare it for injection.

## Permission and live QA gates

Use native Computer Use in Safari after the separately authorized release.
Settings > Extensions must show the intended enabled signed extension; Settings
> Websites > OCDJ Dig must be inspected per host. Declared matches, actual Allow
grants and a loaded signed resource set are three separate facts. Record Ask or
Deny as blocked; change grants only with separate authority. Beatport host access
is a future activation gate. Check OCDJ backend access and embed hosts too;
youtube-nocookie Ask can block playback independently.

Follow all seven live steps in [SAFARI-CAPTURE-14.md](SAFARI-CAPTURE-14.md#exact-remaining-live-qa-native-computer-use-only).
The acceptance matrix includes YouTube watch, SoundCloud track/set (including a
matching nested `/n/` document), Spotify track/album, Beatport track/release,
Bandcamp track/album and Discogs release. Each needs correct, nonduplicated
Wantlist/Queue controls after late render, internal navigation and Back/Forward.
For SoundCloud, require controls above the matching frame, correct artist/title,
and stable controls through waveform mutations. Fixture frame access is not
proof that Safari's content-script isolation permits the same access live.

Use operator-selected recordings. One click per intended action, then verify
actual Wanted persistence (artist, title/release_name, source URL in notes) and
Player queue source/artwork plus audible intended playback. Beatport Queue uses
the first YouTube search result: verify the recording/mix, empty-result failure
and repeat dedup separately. An offline/local save, auth error or checkmark alone
does not pass. Keep #14 open until activation and this matrix pass. Keep #20 open
until its reviewed source scope is merged; distinguish that from backend deploy
and live acceptance. Record only sanitized results in GitHub, never tokens,
account identifiers, private browsing state or production data.

Rollback, if separately needed and authorized: restore the prior signed app and
verify its resource/registration baseline; use the normal backend release PR
process to revert the allowlist change. No database schema migration is involved.
