# TraxDB Auth — Long-Term Fix (scope)

Status: **Option A IMPLEMENTED + PROVEN** 2026-07-09. The headless chain
(refresh token → access token → Blogger API v3) resolves the private blog and
returns pixeldrain links end-to-end from the prod backend container. Cookie path
retained as fallback; `TRAXDB_FETCH_MODE` defaults to `api`. Remaining TODO: the
monthly pixeldrain keepalive guard (see "Keepalive").

## Problem

The TraxDB source blog (`traxdb2.blogspot.com`) is a **private, invite-only Blogspot blog**.
The sync scraper authenticates with Google session cookies exported from the
operator's Chrome (`tools/traxdb_sync/refresh_cookies.py` →
`/srv/ocdj/secrets/traxdb_cookies.txt`).

Verified 2026-07-09: exported cookie snapshots are now **effectively
single-use**. A fresh export worked from the VPS exactly once; within ~5
minutes Google invalidated the entire session globally (the same file went
dead on the Mac too). Cause is consistent with Google session-rotation /
anomaly detection on reuse from a second IP. Anonymous access: hard login
redirect, so there is no cookie-free path.

Today's workaround (documented in session memory): visit blog in Chrome →
export → in-place overwrite on VPS → trigger sync **immediately**. Works, but:

- requires the Mac awake with Chrome logged in, at sync time
- any "verify first, sync later" gap burns the export
- unschedulable; every sync is a manual dance

The second credential, `PIXELDRAIN_API_KEY`, is solved: keys expire 30 days
after *last use*, so any sync cadence ≤ monthly keeps it alive (see
"Keepalive" below for the guard).

## Option A — Blogger API v3 + OAuth refresh token (recommended)

Replace HTML scraping+cookies with the official API and a durable OAuth grant.

- **Auth:** GCP project (new, minimal — unrelated to the decommissioned
  Gemini/CrateMate credentials per ROADMAP D3), OAuth client type "Desktop
  app", scope `https://www.googleapis.com/auth/blogger.readonly`. One-time
  consent in the operator's browser on the Mac; the refresh token then lives
  in the Config store (`BLOGGER_CLIENT_ID` / `BLOGGER_CLIENT_SECRET` /
  `BLOGGER_REFRESH_TOKEN`). Access tokens minted per-run via plain
  `requests` POST to `oauth2.googleapis.com/token` — no Google SDK needed.
- **Data:** `blogs/byurl?url=…` → blogId, then `posts.list` with
  `view=READER`, `fetchBodies=true`, bounded `maxResults`,
  `fields=items(id,url,published,updated,title,content),nextPageToken`
  pagination, and backoff on 429/5xx. Post bodies go through the same
  regexes the scraper uses today — **but** that parsing is currently
  embedded inside `scrape_blog_links()` (`traxdb/services/scraper.py:145`),
  whose flat-text mode scans whole rendered pages, not per-post bodies.
  Step 0 of the implementation is extracting a reusable
  `parse_traxdb_links_from_html(html, source_url, fallback_date)` used by
  both the cookie and API fetchers.
- **Durability:** refresh tokens are durable *enough* if the OAuth app is
  published to Production and the token is used periodically — but Google
  has several invalidation paths (6 months unused, Testing-mode 7-day
  expiry, revocation, live-token caps). Handle `invalid_grant` explicitly
  with a re-bootstrap runbook, not a stack trace.
- **Verification:** `blogger.readonly` is a sensitive scope; personal-use
  through the unverified-app warning screen is acceptable. Do **not** spend
  time on Google's app-verification process unless more users ever need
  access.

### Spike (completed — all green)

The gating spike ran 2026-07-09 against the live private blog and every check
passed: GCP project `ocdj-traxdb` + desktop OAuth client created, consent
screen published to Production, consent granted as `raphaelpalmer42@gmail.com`
(a **reader** of the blog), `blogs/byurl` resolved the private blog,
`posts.list(view=READER, fetchBodies=true)` returned post `content`,
`nextPageToken` pagination worked, pixeldrain/MIRROR1 links extracted, and
dates inferred from post `published`. Reader-access via the API — the biggest
unknown — is confirmed.

### Implementation

| Step | Work | Est | Status |
| --- | --- | --- | --- |
| 0 | Extract `parse_traxdb_links_from_html()` from `scrape_blog_links()`; regression-test against current fixtures | 0.5d | ✅ done |
| 1 | `traxdb/services/blogger_api.py`: token refresh (incl. `invalid_grant` → re-bootstrap error), posts iterator with fields projection + backoff | 0.5d | ✅ done |
| 2 | Wire into `run_sync` behind config flag `TRAXDB_FETCH_MODE=api\|cookies` (cookies path kept as fallback; `api` is the default) | 0.5d | ✅ done |
| 3 | Config schema entries + one-time token bootstrap script (`tools/traxdb_sync/blogger_oauth_bootstrap.py`, runs on Mac, writes the refresh token to a file) | 0.25d | ✅ done |
| 4 | Unit tests: parser against fixture post bodies; token-refresh error paths | 0.25d | ✅ done |
| 5 | Prod verify + retire cookie path & `refresh_cookies.py` docs; re-bootstrap runbook | 0.25d | ⏳ cookie path kept as fallback for now; monthly pixeldrain keepalive still TODO |

**Total: 2–3 days** including the spike. Delivered: the full chain is proven in
prod — a live sync via `TRAXDB_FETCH_MODE=api` returns TraxDB links. The bootstrap
script generalises the working token-mint flow for re-bootstrap on revocation.

### Risks (residual)

- Refresh token revocation (Google-side or manual) → sync fails loudly with
  the re-bootstrap message; runbook is
  `tools/traxdb_sync/blogger_oauth_bootstrap.py`.
- OAuth consent screen misconfig (left in Testing) silently kills refresh
  tokens after 7 days — verified published to Production; noted in the
  bootstrap script docstring.
- Blog owner revokes operator's reader invite — orthogonal, breaks every option.

## Option B — Mac-side fetch agent (fallback)

Keep cookies but never export them: a small launchd-scheduled (or manually
run) script on the Mac reads Chrome's cookie store via `browser_cookie3`
**in-process** and scrapes from the Mac's own IP — the exact conditions under
which cookies demonstrably survive. It then POSTs the scraped link list to a
new bearer-protected VPS endpoint (`POST /api/traxdb/ingest-links/`,
KICK_TOKEN pattern from `organize/auth.py`); the VPS keeps doing Pixeldrain
downloads with its stable API key.

- Pros: no GCP/OAuth, reuses all parsing code, ~1–1.5 days.
- Cons: sync depends on Mac awake + Chrome logged in; two-piece system
  (agent version skew, silent staleness if the launchd job rots); still
  cookie-fragile if Google tightens same-IP replay.

## Option C — Status quo

The documented one-shot dance. Zero dev cost, real friction every sync.
Superseded by Option A; only relevant if the API path ever dies with no
refresh token available (`TRAXDB_FETCH_MODE=cookies`).

## Rejected

- **Headless Chrome profile on the VPS** — Google login in a datacenter
  headless browser is detection-prone and a maintenance sink.
- **Blog RSS/Atom feed** — private blogs gate feeds behind the same auth.

## Keepalive (both options, small)

Monthly scheduled **authenticated `get_list` against a known live list**
(existing Huey periodic-task infra) so `PIXELDRAIN_API_KEY` never crosses
its 30-day idle expiry even if no sync runs. Record the status; log/alert
on 401 so a dead key is noticed before the next sync needs it. ~1h
including test.

## Recommendation

Option A is live: `TRAXDB_FETCH_MODE=api` is the default sync path. The cookie
fallback (`TRAXDB_FETCH_MODE=cookies`) is retained temporarily until the API
path has a few clean prod syncs behind it, then it and `refresh_cookies.py`
can be retired. Remaining work: the monthly Pixeldrain keepalive guard
(see "Keepalive") — still TODO.
