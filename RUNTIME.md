# Runtime — production is on the VPS

**Production runs on the elder-brain VPS, not your Mac.**

This Mac folder is the source clone. It also runs a local compose stack
(`db`, `slskd`, `backend`, `worker`; project `ocdj`) and four LaunchAgents;
[AGENTS.md](AGENTS.md) lists them and how to check them. The local stack keeps
its data in named volumes: never `docker compose down -v` here and never
recreate a volume.

## Where it actually runs

- **VPS deploy type:** plain compose
- **VPS source path:** `/home/ubuntu/ocdj/`
- **Public URL:** https://ocdj.grooveops.dev
- **Logs:** `ssh main-instance "docker logs ocdj-frontend-1"`

## How to debug or query prod

- SSH: `ssh main-instance` then operate at the VPS path above. Production
  DB lives only there.
- Telegram: `@elder_brain_bot` has rw on `/home/ubuntu`, `/etc/dokploy`,
  the docker socket, and `claude` CLI. It can grep code, run `psql`,
  restart containers, push commits, etc.

## How to ship a code change

1. Edit on Mac, commit, push to the branch this project's VPS clone tracks.
2. Dokploy-managed apps redeploy via webhook automatically. Plain-compose
   apps (in `/home/ubuntu/<slug>/`) need a manual `git pull && docker
   compose up -d --build` on the VPS.

## What isn't in this repo

The Traefik auth exemptions on the VPS, the LaunchAgents on this Mac, and the
signing recipe for the Safari extension all live outside git and are the usual
cause of "works here, not there". See
[docs/EDGE-AND-LOCAL.md](docs/EDGE-AND-LOCAL.md).

## Why this exists

A separate Claude session (or a stale memory) tried to spin this project
up locally, hit a Postgres without the prod data, and started proposing
"how do we sync state from prod?" The answer is: don't. Work on prod
directly via SSH or the bot. This file is the canonical reminder.
