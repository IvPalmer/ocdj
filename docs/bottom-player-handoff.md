# Shared bottom player — issue #13

Stack base: PR #19, exact original head `49a92d9ea28d980c9f2f7949e5beada991597129`.
This increment owns playback and track controls. Collection transport, recognition,
backend metadata, Safari, and Soulseek history clearing are outside its scope.

The app mounts one provider and player above route content. Play and Queue append
an actual source; the player keeps the current track, history and upcoming entries
across section changes, with previous/next, pause/play, native seek controls and
end-of-source advancement. Refreshing the browser starts a fresh queue.

Source coverage:

- Instagram cached video audio and exact candidate catalog previews use the shared
  native player. The original video preview, cache refresh flow, review/correction,
  wishlist actions and routing remain available. Video and player pause each other.
- Shazam/recognized track rows and description tracks use the existing source mix
  through YouTube/SoundCloud, starting at the identified timestamp. These are mix
  segments, not a claim to have obtained an isolated full track. Other source
  platforms explicitly show that no supported audio source exists.
- Crate-Mate direct YouTube matches can play/queue. Search/catalog links remain
  links and are not treated as streams.
- Wanted, import preview and Library rows share the playback component. Current
  Wanted/Library serializers expose metadata/local paths, without stream URLs;
  those rows explicitly label the missing browser source. Filesystem paths are
  never used as audio URLs. No stream backend was added.
- The player uses existing Wanted creation and Soulseek queue APIs. Soulseek adds
  a search queue entry and opens the existing Soulseek route; it does not clear
  history or silently start downloads/searches.

Validation:

- `npm ci --no-audit --no-fund` and `npm run build`.
- `git diff --check`.
- `tools/test_bottom_player.py` runs isolated Chrome fixtures with synthetic API
  responses and real native WAV playback. It covers route persistence,
  queue/previous/next/end, pause/play, playback error retry, video/player exclusion,
  Wanted/Soulseek request payloads, missing-source labels, mobile width, lower
  sidebar navigation and YouTube/SoundCloud adapter calls/start timestamps.
- External YouTube/SoundCloud playback is stubbed in this fixture; real provider
  access, live Instagram cache streams and physical audio output are unverified.
- The existing Instagram browser fixture now locates the shared audio element
  and expects it to persist when a later catalog lookup finds no preview. The
  Django/PostgreSQL suite remains a CI check, separate from the isolated fixture.

Reproduce locally with the existing Python Playwright installation and Chrome:

```sh
npm run dev -- --host 127.0.0.1 --port 5186
python3 tools/test_bottom_player.py http://127.0.0.1:5186
```

Review/release: stacked increment only; no merge or deployment authorized. Retarget
or restack after PR #19 lands. Remove this increment to roll back its frontend
behavior; there are no migrations, config changes or new dependencies.
