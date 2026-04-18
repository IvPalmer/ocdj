# OCDJ Chat Sidecar

FastAPI service that runs the in-app Agent (Claude Agent SDK). Exposes `/chat`
as an SSE stream and `/health` for the frontend's "Sidecar connected" badge.

## Why a sidecar (not a container)

The `claude-agent-sdk` authenticates through the local `claude` CLI, which
reads `~/.claude/` and uses the user's Max subscription. Keeping this on the
host avoids mounting `~/.claude/` read-only into the Docker backend container
and the tangled Node+SDK install inside it. The sidecar talks to the
Dockerized Django backend at `http://localhost:8002` for all OCDJ state.

## Start

```
./run.sh
```

First run: creates `.venv`, installs deps, boots on `http://127.0.0.1:5179`.
Subsequent runs reuse the venv. Vite's dev server proxies `/sidecar/*` to
`host.docker.internal:5179` so the React app at `http://localhost:5174/agent`
can reach it.

## Auth

No API key required. The SDK finds the `claude` CLI on your `PATH` and
re-uses its auth. Confirm you're logged in first:

```
claude --version
```

## Architecture

```
React AgentPanel          fetch('/sidecar/chat', body: {message, reset})
        │
        ▼
Vite dev proxy            http://host.docker.internal:5179
        │
        ▼
FastAPI server.py         ClaudeSDKClient with streaming SSE
        │
        ▼
ocdj_tools.py             MCP server (in-process)
        │
        ▼
httpx -> Django API       http://localhost:8002/api/*
```

Each `/chat` call reuses the prior `session_id` so multi-turn context works.
Pass `{"reset": true}` or `POST /reset` to start a new session.

## Tools (as of 2026-04-17)

Read:
- `get_stats` — dashboard + health
- `list_wanted(status, search, limit)`
- `list_library(search, format, limit)`
- `list_recognize_jobs(status, limit)`
- `list_pipeline_items(stage, limit)`
- `get_config(key)`
- `find_stuck_jobs(max_age_hours)` — recognize jobs stuck in `recognizing`
- `find_duplicates_wanted(similarity)` — fuzzy dupe detection

Write:
- `update_wanted(item_id, fields)`
- `delete_wanted(item_id)`
- `promote_track(track_id)` — copy to REVIEW_FOLDER
- `scan_downloads` / `scan_library`
- `audit_music_root(apply, reclassify)`

Plus built-ins: `WebFetch`, `WebSearch`.

Every tool name reaches the model as `mcp__ocdj-tools__<name>`. To add a
new one: declare it in `ocdj_tools.py` (Tool + call_tool case + ALLOWED_TOOLS
entry), restart the sidecar.

## Troubleshooting

- **"Sidecar unreachable" in the UI** — run `./run.sh` on the host. Check
  the log at `/tmp/ocdj-sidecar.log` if launched via nohup.
- **Tools return `Connection refused` on 172.x.x.x** — the Django backend
  is down. Start it with `docker compose up backend`.
- **`claude` CLI not found** — install Claude Code / Max; the SDK depends on
  it for auth.
- **Max auth expired** — re-run `claude login` on the host; the sidecar
  will pick up the refreshed token on the next request.
