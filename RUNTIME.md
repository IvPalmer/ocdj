# Runtime — VPS-only

**This project runs on the elder-brain VPS, not your Mac.**

This Mac dir is a **source clone for editing context only**. Local Docker
is disabled here. Never run:

- `docker compose up` / `make dev` / anything that boots a local DB or API.
- A previous "local dev" stack used to live here — it's been torn down and
  the named volumes nuked. Do not reanimate it.

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

## Why this exists

A separate Claude session (or a stale memory) tried to spin this project
up locally, hit a Postgres without the prod data, and started proposing
"how do we sync state from prod?" The answer is: don't. Work on prod
directly via SSH or the bot. This file is the canonical reminder.
