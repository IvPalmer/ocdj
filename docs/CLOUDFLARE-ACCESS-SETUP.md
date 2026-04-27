# Cloudflare Access — `ocdj.grooveops.dev`

**Status:** plan written 2026-04-27, **not yet shipped**. ~15 min when you do this. Everything below is what to click.

## Why this exists

The download-mint endpoint (`POST /api/organize/pipeline/<id>/download-url/`) has zero authz. Pipeline IDs are sequential integers — anyone who knows the URL pattern + a valid ID can mint a 120-second signed download URL and pull a track. Codex flagged this as an IDOR during the 2026-04-23 hardening review.

The fix is **not in code**. Cloudflare Access sits in front of the whole site at the CDN edge: every hit to `ocdj.grooveops.dev` gets bounced to a Google login first, only your Google account gets through, the Django app sees authenticated traffic only, the IDOR becomes inaccessible.

## Architecture after this lands

```
browser → ocdj.grooveops.dev
         ↓ Cloudflare edge (Access policy: Google IdP, allow palmer@…)
         ↓ Traefik on VPS
         ↓ Django/Next.js app

Mac drain LaunchAgent → /api/drain/* and /api/organize/pipeline/kick/
                       ↓ Cloudflare edge (Access policy: Bypass for these paths)
                       ↓ Traefik on VPS
                       ↓ Django (still gated by X-Drain-Token header on its own)
```

Two Access policies on one app:
1. **Allow** — interactive browser traffic, Google login required.
2. **Bypass** — headless drain endpoints (no browser, can't pass an interactive login). They're already gated by a backend shared-secret header.

## Steps

### A. Set up the Access app (one-time)

1. Open https://one.dash.cloudflare.com/ (Zero Trust dashboard).
2. Pick the team / account that owns `grooveops.dev`. First Access setup: prompt to create a Zero Trust team — name it `grooveops`, free tier covers 50 users.
3. Left nav → **Settings → Authentication → Login methods → Add new**.
4. Choose **Google**. Wizard walks you through creating an OAuth Client in Google Cloud Console — paste the resulting Client ID + Client Secret back into Cloudflare. Authorized redirect URI Cloudflare gives looks like `https://<team>.cloudflareaccess.com/cdn-cgi/access/callback`. Save.
5. Left nav → **Access → Applications → Add an application → Self-hosted**.
   - Application name: `ocdj`
   - Session duration: 24 h (your call — 8 h is paranoid, 24 h is convenient)
   - Application domain: `ocdj.grooveops.dev`
   - Identity providers: tick **Google** only
   - Click **Next**
6. Add an Access policy:
   - Policy name: `me`
   - Action: **Allow**
   - Configure rules → Include → Selector **Emails** → enter `raphaelpalmer42@gmail.com` (or whichever Google account you actually use)
   - Click **Next** → **Add application**

### B. Bypass policy for the Mac drain endpoint

The Mac drain LaunchAgent hits `/api/drain/` and `/api/organize/pipeline/kick/` headlessly — those calls have **no browser**, so they can't pass an interactive Google login. They need a bypass.

7. Open the `ocdj` app in Cloudflare Access → **Add a policy**.
   - Policy name: `drain-bypass`
   - Action: **Bypass**
   - Configure rules → Include → Selector **Everyone**
   - Path constraint at the bottom: `/api/drain/` (and a separate policy for `/api/organize/pipeline/kick/` if you want them split — single bypass policy with a regex works too)
   - Save

   Bypass = no auth required for that path. The drain endpoint already has its own shared-secret check on the backend (`X-Drain-Token` header) so it's not unauthenticated — just gated differently from the browser flow.

   **Important:** *only* `/api/drain/` and the `kick` endpoint should bypass. Do NOT bypass `/api/organize/pipeline/<id>/download-url/` — that's the IDOR you're closing.

### C. Verify

8. Open `https://ocdj.grooveops.dev/` in a fresh browser / incognito → should redirect to a Cloudflare Access login → Google login → land on the app.
9. Curl the download-mint without auth to confirm the IDOR is closed:
   ```sh
   curl -i https://ocdj.grooveops.dev/api/organize/pipeline/1/download-url/ -X POST
   ```
   Should return Cloudflare's auth challenge HTML (HTTP 302 → access.cloudflareaccess.com), **not** the Django backend's response.
10. SSH to main-instance and run a Mac-drain test:
    ```sh
    curl -i -X POST https://ocdj.grooveops.dev/api/organize/pipeline/kick/ -H "X-Drain-Token: $TOKEN"
    ```
    (Or just wait 5 min for the LaunchAgent to fire and check `tail -f` on its log.) Should still work — bypass policy passes it through.

### D. After it's live

- Add `~/Work/Dev/elder-brain/wiki/decisions/2026-04-27-cf-access-ocdj.md` recording app name, allowed emails, bypass paths, session duration. Future-Palmer will want this.
- The `download-url/` IDOR codex flagged stays *technically* present in the Django code — no authz check on the mint endpoint. Cloudflare gates everything before the request reaches Django. **If you ever expose any other ocdj path publicly (e.g. without going through Cloudflare), the IDOR comes back.** Keep this in mind for future work.

## Pointers

- Codex IDOR flag in elder-brain session log: `~/Work/Dev/elder-brain/docs/SESSION-LOG.md` 2026-04-23 entry, "Still open from codex".
- Mac drain LaunchAgent: `~/Library/LaunchAgents/dev.grooveops.ocdj-drain.plist` (and `.ocdj-incoming.plist` for the reverse direction).
- Drain shared-secret env: `~/.config/ocdj/drain.env` on Mac.
