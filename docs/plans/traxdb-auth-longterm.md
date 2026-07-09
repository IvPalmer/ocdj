# TraxDB Auth — Long-Term Fix (scope)

Status: scoped 2026-07-09, not started. Owner: operator decision on Option A vs B.

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

### Spike first (half-day, gates everything)

1. Create GCP project + OAuth desktop client, publish consent screen to
   Production (checklist item: NOT left in "Testing").
2. Consent as `raphaelpalmer42@gmail.com` (a **reader**, not author, of the blog).
3. Against real TraxDB data, all of: `blogs/byurl` resolves the private
   blog → `posts.list(view=READER, fetchBodies=true)` returns post
   `content` → pagination via `nextPageToken` works → pixeldrain/MIRROR1
   links extract → dates infer (from post `published` or body text).

**Kill criterion:** any of those five failing on the private blog kills
Option A → pivot to B. Reader-access is the biggest unknown, but a green
spike requires the full chain, not just auth.

### Implementation (after green spike)

| Step | Work | Est |
| --- | --- | --- |
| 0 | Extract `parse_traxdb_links_from_html()` from `scrape_blog_links()`; regression-test against current fixtures | 0.5d |
| 1 | `traxdb/services/blogger_api.py`: token refresh (incl. `invalid_grant` → re-bootstrap error), posts iterator with fields projection + backoff | 0.5d |
| 2 | Wire into `run_sync` behind config flag `TRAXDB_FETCH_MODE=api\|cookies` (cookies path kept as fallback until A proves out in prod) | 0.5d |
| 3 | Config schema entries + one-time token bootstrap script (`tools/traxdb_sync/oauth_bootstrap.py`, runs on Mac, prints the three config values) | 0.25d |
| 4 | Unit tests: parser against fixture post bodies; token-refresh error paths | 0.25d |
| 5 | Prod verify + retire cookie path & `refresh_cookies.py` docs; re-bootstrap runbook | 0.25d |

**Total: 2–3 days** including the spike.

### Risks

- Reader-access-via-API unknown → that's why the spike is first.
- OAuth consent screen misconfig (left in Testing) silently kills refresh
  tokens after 7 days — checklist item in the bootstrap script.
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
Acceptable interim while A is spiked; not a destination.

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

Run the Option A spike (half-day). Green → build A, retire cookies.
Red → build B. Either way add the keepalive. Interim: Option C dance,
procedure in session memory (`traxdb_dual_credentials`).
