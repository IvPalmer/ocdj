# OCDJ

Where: music / research / ocdj, `~/Dev/music/research/ocdj` (manifest row `ocdj`, lead `ocdj-lead`).

Read RUNTIME.md and docs/ROADMAP.md first: where it runs and how to ship, then goal, status, next step and open tasks. docs/EDGE-AND-LOCAL.md covers the Traefik auth exemptions, the Mac LaunchAgents and the Safari extension build.

## What this is

DJ music acquisition: Wanted lists, recognition, Soulseek downloads, TraxDB sync, Organize (clean, tag, convert) and the library. Django backend in `backend/` with a Huey worker, React/Vite frontend in `src/`, Mac daemons and sync tools in `tools/`. Production is https://ocdj.grooveops.dev (oauth2-proxy in front), a plain compose stack on the VPS.

## Shipping

- Changes go as PRs to `vps-deploy`, the branch the VPS clone tracks. `main` trails it.
- After a merge the VPS needs a manual pull and rebuild; RUNTIME.md has the command.
- The Mac checkout's code is live: the local containers bind-mount `backend/` and `tools/`, and the TraxDB LaunchAgent runs `tools/traxdb_sync/` from this folder. Switching branches here changes what they run.

## Mac runtime

Production data lives only on the VPS, and the VPS never stores audio: the Mac does.

LaunchAgents `dev.grooveops.ocdj-{drain,incoming,traxdb-local,yt-local}` run the scripts in `~/bin/` (copies in `tools/mac-daemons/` and `tools/traxdb_sync/`; copy a script back after editing it). Only `ocdj-traxdb-local` runs code from this folder, through `REPO_ROOT` in its wrapper. Its `.venv` was made under an older folder name, so in practice the wrapper runs Homebrew `python3`.
- Check: `launchctl print gui/$(id -u)/dev.grooveops.ocdj-<name>` shows `last exit code = 0`; logs in `~/Library/Logs/ocdj-*.log`. `yt-local` is silent when it has no jobs.

Local compose stack, project `ocdj`: `db`, `slskd`, `backend`, `worker`, all `restart: always` (`frontend` is not run). Data sits in the named volumes `ocdj_postgres_data`, `ocdj_huey_volume` and `ocdj_static_volume` and in the `~/Music/Musicas` binds.
- Never `docker compose down -v` and never recreate a volume. The project name comes from this folder's name; start the stack from here with `docker compose -p ocdj up -d db slskd backend worker`.
- Check: `curl -s http://127.0.0.1:8002/api/core/health/` returns `"status":"ok"`, the slskd UI answers on :5030, and `docker logs ocdj-slskd-1` shows it logged in. The local slskd uses its own Soulseek account, never the operator's personal one.

## Working rules

- Every tunable a future user would set (API keys, playlists, destination paths, thresholds) lives in the DB-backed `core.Config` store through `get_config()`, with an env fallback. No new per-module constants.
- Long jobs (recognize, Soulseek, TraxDB, Organize batches) run on the Huey worker so they survive a backend restart. No raw `threading.Thread` for work that must finish.
- TraxDB is a separate archive under `.../ID3/traxdb/YYYY-MM-DD/`. It never feeds Organize automatically.
- A TraxDB date folder that exists is closed, an empty one included: the operator prunes tracks by hand, so never re-download or fill gaps into it.
- TraxDB audio downloads go to the Mac (`ocdj-traxdb-local`), never to the VPS. `TRAXDB_DOWNLOAD_TARGET` fails closed.
- The Shazam feed comes through Spotify, never Apple Music (it would pollute the curated Music.app library).
- Music.app integration only adds files; classification into playlists stays a suggest-and-accept step.
- A single-file bind mount pins the inode: rewrite such a file in place or recreate the container, then check from inside the container.
- nginx proxies the backend through a variable (`resolver` plus `proxy_pass $ocdj_backend`), so a recreated backend does not leave the UI disconnected.
- Safari extension: notarization and the App Sandbox are mandatory; the full recipe is in docs/EDGE-AND-LOCAL.md.

## Tests

- Backend: `docker exec ocdj-backend-1 sh -c 'cd /app && python manage.py test --noinput'` (uses a throwaway test database).
- TraxDB daemon: `cd tools && python3 -m pytest -q traxdb_sync/test_local_daemon.py`.
- Frontend: `npx vite build`.

## Lead

`ocdj-lead` (Paperclip, reports to `product-chief`) leads this product: its goals, backlog, delivery and verified user outcome. It keeps the issues current, delegates to `ocdj-worker-claude` and `ocdj-worker-codex`, and reports results and real blockers to `product-chief`. This file is its project brief; `docs/ROADMAP.md` is its backlog. Nothing reaches production until a `vps-deploy` PR is merged and the VPS is rebuilt.
