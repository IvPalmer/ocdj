# Mac daemons

Backup copies of the LaunchAgent scripts and plists that move audio between
this Mac and the VPS. The Mac holds the library; the VPS never keeps audio, so
if these are lost the pipeline has no way home.

**`~/bin/` and `~/Library/LaunchAgents/` are the live copies.** These are
backups, not the source: launchd runs the ones in the home directory. After
editing a live script, copy it back here — nothing enforces that, and a stale
copy is worse than none because it looks authoritative.

`ocdj-traxdb-local.sh` is deliberately not here; it lives with the tool it
belongs to, under `tools/traxdb_sync/`.

No secrets: every one of these reads its bearer token from
`~/.config/ocdj/*.env`, which is not in this repo and should not be.

| script | every | what it does |
|---|---|---|
| `ocdj-incoming.sh` | 5 min | uploads `_incoming/` to the VPS, kicks the pipeline |
| `ocdj-drain.sh` | 5 min | rsyncs finished tracks back to `…/ID3/_review` |
| `ocdj-yt-local.sh` | 15 min | pulls YouTube download jobs |

See [../../docs/EDGE-AND-LOCAL.md](../../docs/EDGE-AND-LOCAL.md) for how these
fit with the Traefik exemptions that let them authenticate at all.
