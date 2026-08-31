# What runs outside this repo

Three things this project depends on are not in git and would be lost, or
silently wrong, if the machine holding them were rebuilt: the Traefik auth
exemptions on the VPS, the LaunchAgents on the Mac, and the signing recipe for
the Safari extension. Each has bitten already; this is what to check first when
something "works locally but not deployed", or the reverse.

See also [RUNTIME.md](../RUNTIME.md) for where the app itself runs.

## Traefik auth exemptions (VPS)

`/etc/dokploy/traefik/dynamic/ocdj-auth.yml` — **not in git**, dated backups
sit beside it.

Everything on `ocdj.grooveops.dev` is behind oauth2-proxy (Google, single
allowed address). Routers with `priority: 300+` carve out narrow paths that
machines have to reach and that cannot complete a browser sign-in. Each is
bearer-authed in the app itself and returns 503 if its token is unset, so the
exemption strips the oauth requirement and nothing else.

| router | path | who calls it |
|---|---|---|
| `ocdj-drain` | `/api/drain/` | Mac drain daemon |
| `ocdj-kick` | `/api/organize/pipeline/kick/` (POST) | Mac incoming daemon |
| `ocdj-ytfetch-local` | `/api/ytfetch/pending-local/`, `<id>/deliver-local/`, `<id>/meta/` | Mac YouTube daemon |
| `ocdj-traxdb-local` | `/api/traxdb/local/{inventory,claim,<id>/complete,<id>/fail}/` | Mac TraxDB daemon |
| `ocdj-shazam-ingest` | `/api/wanted/shazam/ingest/` | anything pushing Shazams |
| `ocdj-cors-preflight` | any `/api/` with `OPTIONS` | browser extensions |

The preflight rule is the least obvious. A CORS preflight carries no cookies,
so oauth2-proxy answered every `OPTIONS` with 401 and the browser never sent
the real request — which made the extension look broken while the endpoint was
fine. A preflight response carries no data, only which methods and headers are
allowed; the request that follows is authenticated normally.

Verify after any change to that file:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://ocdj.grooveops.dev/api/wanted/items/          # 401
curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS https://ocdj.grooveops.dev/api/wanted/items/ \
  -H 'Origin: chrome-extension://aaaa' -H 'Access-Control-Request-Method: POST'               # 200
```

An exemption that stops returning 401 without a token is a hole, not a
convenience. Check both lines, not just the one you were adding.

## Mac LaunchAgents

`~/Library/LaunchAgents/dev.grooveops.ocdj-*.plist`, scripts in `~/bin/` —
those are the copies launchd actually runs. Backups live in
`tools/mac-daemons/` (and `tools/traxdb_sync/` for the TraxDB one); nothing
keeps the two in step, so copy a script back after editing it.

| agent | every | what it does |
|---|---|---|
| `ocdj-incoming` | 5 min | uploads `_incoming/` to the VPS, kicks the pipeline |
| `ocdj-drain` | 5 min | rsyncs finished tracks back to `…/ID3/_review` |
| `ocdj-traxdb-local` | 15 min | reports Mac inventory, downloads TraxDB lists from Pixeldrain |
| `ocdj-yt-local` | 15 min | pulls YouTube jobs |

The Mac is where audio lives; the VPS never keeps it. Anything reporting an
empty library is usually a daemon that has stopped, not data loss —
`launchctl list | grep grooveops` and the logs in `~/Library/Logs/ocdj-*.log`.

`ocdj-drain.sh` pulls with `rsync -a`, deliberately without `-z`. AIFF and FLAC
are already incompressible, so compression only costs throughput: measured on a
144 MB AIFF from this VPS, `-az` gave 16.5 MB/s and `-a` gave 37 MB/s.

## Safari extension: build and signing

Source in `ocdj-helper/` (the connected variant) and `OCDJ Helper/` (the Xcode
wrapper whose `Resources/` is a copy of it — they are kept in step by hand).

The build recipe matters because four separate things each blocked it silently:

```bash
xcodebuild -project "OCDJ Helper/OCDJ Helper.xcodeproj" \
  -scheme "OCDJ Helper" -configuration Release \
  -derivedDataPath "OCDJ Helper/build" build \
  CODE_SIGN_IDENTITY="Developer ID Application: Raphael Palmer (C4SJBQAUZY)" \
  CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM=C4SJBQAUZY \
  CODE_SIGN_INJECT_BASE_ENTITLEMENTS=NO \
  OTHER_CODE_SIGN_FLAGS="--timestamp --options=runtime"
```

Then notarize — an unnotarized Developer ID app is refused by Gatekeeper, never
launches, and Safari never offers an extension whose container app cannot run:

```bash
ditto -c -k --keepParent "<app>" /tmp/ocdj-helper.zip
xcrun notarytool submit /tmp/ocdj-helper.zip \
  --key ~/.appstoreconnect/private_keys/AuthKey_U8W6Z262WJ.p8 \
  --key-id U8W6Z262WJ --issuer 0871034f-31f5-41f0-91bb-b0f3ed0bba67 --wait
xcrun stapler staple "<app>"
```

The four traps, in the order they appeared:

1. **Ad-hoc signing** registers with `pluginkit` but Safari lists it as
   `UNSIGNED`, which only appears while the Develop menu toggle is on — and
   that resets every launch.
2. **`get-task-allow`** — xcodebuild injects the debug entitlement and
   notarization rejects it. Hence `CODE_SIGN_INJECT_BASE_ENTITLEMENTS=NO`.
3. **App Sandbox** — that same flag strips *every* entitlement, and the project
   had no `.entitlements` files of its own. Without
   `com.apple.security.app-sandbox`, PlugInKit refuses to register a Safari
   web extension at all, so it never reaches Safari. Both targets now carry
   explicit entitlements files; check them before blaming Safari:

   ```bash
   codesign -d --entitlements - "/Applications/OCDJ Helper.app/Contents/PlugIns/OCDJ Helper Extension.appex"
   ```

4. **A stale build shadowing the real one.** `pluginkit` registered an old
   Debug build inside `build/Build/Products/`, so Safari kept reading that copy
   and the extension "appeared and disappeared" across rebuilds. Delete stale
   build products; do **not** use `pluginkit -r` to fix it — that removes the
   registration and nothing re-registers it until the next login.

Confirm the registration points at `/Applications`, not a build folder:

```bash
pluginkit -m -A -D -vvv -p com.apple.Safari.web-extension -i com.ocdj.OCDJHelperApp.SafariExt
```

The extension authenticates by session cookie (`credentials: 'include'`), so it
inherits the browser's oauth2-proxy sign-in rather than carrying a token of its
own. Safari additionally requires the user to grant the extension access to
`ocdj.grooveops.dev`; without that the request never leaves the browser and
nothing appears in the server logs at all.
