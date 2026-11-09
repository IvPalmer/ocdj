# SoundCloud inline recording controls (#14)

This slice starts from deployed `vps-deploy` commit `1fa67c3`. The reported
Safari corner overlay is replaced by one Queue/Wantlist pair immediately after
the actual `h1` in the matching same-origin `/n/<artist>/<track>` document.
The pair stays in normal document flow, with its heading displayed inline.
No fixed positioning or page-corner overlay remains for these controls.

A frame ownership marker suppresses competing frames and legacy outer-page
controls. A single observer initialization prevents repeated script evaluation
from installing competing adapters. Detached headings/groups are rebuilt;
missing metadata, outer navigation, and frame removal clear stale controls.
Existing Queue embed and Wantlist messages preserve artist, title, canonical
outer recording URL, and SoundCloud source identity.

The only additional content-script declaration is CSS-only, on exact
`https://soundcloud.com/n/*` and `https://www.soundcloud.com/n/*` matches, with
`all_frames: true`. JavaScript stays in the top document. Existing host
permissions, authentication, options, and all other site adapters are unchanged.
This is not a request to grant Safari access or change any user settings.
Connected and wrapper frame adapters and CSS are byte-identical. Safari-specific
native API embeds and playlist visibility/deduplication behavior are preserved.

Versions: extension manifest `1.1.1`; wrapper and appex `1.0.1` (build `2`).
Use the existing [signing recipe](EDGE-AND-LOCAL.md), with build-only
`MACOSX_DEPLOYMENT_TARGET=26.2`. Keep derived data and notarized artifacts
outside the repository in a private directory. No installation, launch,
Safari setting/grant changes, merging, or deployment are part of this slice.

The build at `0fee201eb2ad5ddf2305d34040629c01efc2a463` succeeded, but Xcode
27.2 ignored the attempted `REGISTER_APP_WITH_LAUNCH_SERVICES=NO` override
and automatically registered the isolated build with LaunchServices. No manual
registration/removal was performed. Parent must verify Safari-selected bundle
identity before live QA. This documentation follow-up changes no built source.

## Offline proof

All 42 extension fixtures pass in both active trees. SoundCloud coverage includes
late mount, simultaneous legacy controls, multiple matching frames, repeated
script evaluation, current action payloads, unrelated frame mutations, title
changes, detached-heading remount, missing metadata/recovery, outer navigation,
Back, frame removal, and rejection of an unrelated recording frame.
Beatport fixture readers now accept valid CSS-only manifest entries.

```sh
OCDJ_TEST_JSDOM_PACKAGE=/path/to/existing/package.json \
  node --test tools/extension-tests/*.test.mjs
git diff --check
```

All active JavaScript syntax checks pass. A fully mocked Chromium render at
1280px and 390px verifies one visible, nonoverlapping pair inside the frame,
normal positioning, no outer pair, and no intersection with a fixture playback
bar. Synthetic screenshots and machine-readable bounds remain private with the
build evidence. This proves fixture behavior, not Safari isolated-world frame
access or real SoundCloud layout compatibility.

Parent owns installed-device verification. Before calling #14 accepted, verify
this exact signed bundle in Safari, controls beside the real matching title,
no duplicates through navigation/remounts, and intended Queue playback and
Wantlist persistence. #14 stays open. See the historical release and capture
handoffs for broader outstanding site QA; their above-frame/corner placement
notes are superseded for this slice.
