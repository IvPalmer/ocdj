# Install OCDJ on your own computer

A local install: everything runs in Docker on one machine and the app is at
http://localhost:5174. Works on Windows and macOS.

If you are a coding agent doing this for someone: run the steps in order, stop
at each "Check" until it passes, and ask the person for the values in step 3.
Never invent credentials. Ignore the VPS, LaunchAgent and Safari sections of
`AGENTS.md`, `RUNTIME.md` and `docs/EDGE-AND-LOCAL.md`: they describe the
maintainer's machines, not this one.

## 1. Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/), running.
  On Windows it needs the WSL 2 backend (the installer sets it up).
- Git.
- About 5 GB of free disk for the images, plus room for the music.

Check: `docker compose version` prints a version and `docker ps` does not error.

## 2. Get the code

```bash
git clone -b vps-deploy https://github.com/IvPalmer/ocdj.git
```

```bash
cd ocdj
```

On Windows, clone into a short path without spaces (for example
`C:\dev\ocdj`).

## 3. Configure

Copy `.env.example` to `.env` and set:

| Variable | Needed for | Where it comes from |
| --- | --- | --- |
| `OCDJ_MUSIC_DIR` | everything (required) | A folder you choose for the music. Forward slashes, also on Windows: `C:/Users/you/Music/ocdj`. Create it first. |
| `SLSKD_SLSK_USERNAME`, `SLSKD_SLSK_PASSWORD` | Soulseek downloads | A Soulseek account. Any new username and password pair registers itself on first login. Use one made for this app, not an account you use in another Soulseek client at the same time. |
| `SLSKD_SHARE_DIR` | Soulseek sharing (optional) | A folder of music you share back. Many Soulseek users refuse uploads to people who share nothing. |
| `ACOUSTID_API_KEY` | fingerprint matching in Organize (optional) | https://acoustid.org/new-application |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | Spotify playlist import (optional) | https://developer.spotify.com/dashboard, redirect URI `http://localhost:8002/api/wanted/import/spotify/callback/` |

Everything else in `.env.example` can stay empty. Most keys can also be set
later in the app under Settings, which stores them in the database.

`.env` holds secrets and is ignored by git. Do not commit it.

## 4. Start

```bash
docker compose --profile soulseek up -d --build
```

The first run builds the backend image and installs the frontend packages, so
it takes several minutes. Without `--profile soulseek` the Soulseek daemon is
left out.

Check:

- `curl http://localhost:8002/api/core/health/` returns `"status":"ok"`.
- http://localhost:5174 shows the dashboard. Give the frontend a minute or two
  after the first start: `docker compose logs -f frontend` shows when Vite is
  ready.
- http://localhost:5030 shows the slskd UI, and `docker compose logs slskd`
  shows it logged in to Soulseek.

Once the checks pass, read [HOW-IT-WORKS.md](HOW-IT-WORKS.md): it explains the
flow and the questions to ask the person before configuring anything else.

## 5. Where the music goes

Inside `OCDJ_MUSIC_DIR`:

- `Electronic/ID3/soulseek/` is where Soulseek downloads land and where the
  Organize pipeline works on them.
- `Electronic/ID3/soulseek/collection/` is the folder shared on Soulseek,
  unless `SLSKD_SHARE_DIR` points somewhere else.
- `Electronic/_Review/` is the review staging folder.

The paths can be changed under Settings in the app. They are paths inside the
containers, so they start with `/music`.

## 6. Day to day

- Stop: `docker compose --profile soulseek stop`
- Start again: `docker compose --profile soulseek up -d`
- Update: `git pull`, then `docker compose --profile soulseek up -d --build`
- Logs: `docker compose logs -f backend worker`

The database lives in Docker volumes. `docker compose down -v` deletes it:
never run it unless you mean to wipe the app's data.

## Optional pieces

- **Browser extension (Chrome, Edge, Brave):** load the `ocdj-helper` folder
  under `chrome://extensions` with Developer mode on ("Load unpacked"), then
  set the backend URL in the extension's options to `http://localhost:8002`.
- **Agent tab:** `ocdj-sidecar/` is a small host process that needs the
  `claude` CLI signed in on the host. See `ocdj-sidecar/README.md`. Its
  `run.sh` is a bash script; on Windows run it from WSL or Git Bash.
- **TraxDB:** needs your own blog cookies and a Pixeldrain API key. By default
  the backend does not download TraxDB audio itself; the maintainer uses a
  separate Mac daemon for that. Treat it as unsupported in a local install
  until you have read `tools/traxdb_sync/README.md`.

## Not part of a local install

- The Safari extension: it is signed with the maintainer's Apple developer
  team.
- The macOS LaunchAgents in `tools/mac-daemons/` and the Music.app
  integration.
- `docker-compose.prod.yml`, `nginx.prod.conf` and anything about the VPS.

## Troubleshooting

- `required variable OCDJ_MUSIC_DIR is missing a value`: `.env` is missing or
  the variable is empty.
- `relation "..." does not exist` in the worker log right after the very first
  start: the worker ran a periodic task before the backend finished creating
  the database tables. It recovers on its own on the next run.
- A port is already in use: the stack needs 5174, 8002, 5030, 50300 and 5433
  free on the host.
- Windows, shell script errors mentioning `\r`: the checkout converted line
  endings. Run `git config core.autocrlf input`, delete the working copy and
  clone again.
- Soulseek searches return nothing: check `docker compose logs slskd` for a
  login error, and that port 50300 is not blocked by the firewall.
