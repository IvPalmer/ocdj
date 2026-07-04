---
goal: 'Manage DJ music acquisition: wants, recognition, Soulseek downloads, TraxDB sync, organization,
  and library.'
owner: operator
lead: ocdj-lead
status: active
next: 'Resolve branch policy per D1: fast-forward main to vps-deploy, delete feat/cratemate-module.'
decisions_needed: []
blocked_by:
- Operator approval of branch, credential, auth-doc, and launch-posture decisions.
---

## Open tasks

- [ ] Decide and record canonical branch policy for `vps-deploy`, `main`, and parked `feat/cratemate-module`; evidence: `feat/cratemate-module` is ancestor of `vps-deploy` but not `main`, and recent CrateMate/KICK_TOKEN commits live only on `vps-deploy` [T-001]
- [ ] Bring branch state into the chosen policy by merging, parking, or deleting `feat/cratemate-module` after operator approval [T-002]
- [ ] Verify deployed CrateMate `/api/cratemate/status/` and one cover-identify path against production envs; confirm `CLAUDE_CODE_OAUTH_TOKEN`, `CRATEMATE_DISCOGS_TOKEN`, Spotify credentials, and `CRATEMATE_VISION_MODEL` are real, not placeholders [T-003]
- [ ] Rotate or explicitly retire legacy CrateMate GCP/Gemini credential paths and document whether ResNet remains deferred, since `backend/cratemate` now defaults to Claude vision [T-004]
- [ ] Replace or archive `docs/CLOUDFLARE-ACCESS-SETUP.md` with the actual oauth2-proxy plus bearer-protected `KICK_TOKEN` bypass model; include the 2026-07-04 commit `2b68249` receipt [T-005]
- [ ] Add focused regression tests for non-secret pipeline behavior: Soulseek scoring/query simplification, organize stage movement, recognize clustering, and cross-module Wanted to Download to Pipeline state sync [T-006] #autonomous-safe
- [ ] Finish the H7 low-risk refactor/polish backlog: shared `StatusBadge`, Dashboard recent activity, Library density toggle, shared Pipeline component, and Soulseek filename cleanup [T-007] #autonomous-safe
- [ ] Design the H8 SourceAdapter and scheduler migration for SoundCloud Likes, YouTube Watch Later, Shazam history, and Safari Tab Group before touching schemas or OAuth flows [T-008]
- [ ] Build a TraxDB-to-library adoption/reporting bridge that respects the current decision "TraxDB stays separate archive" while surfacing probably-want tracks in the workflow [T-009]

## Decisions (2026-07-04, HQ with operator veto)

- **D1 Branches:** fast-forward `main` to `vps-deploy` so they're equal; `vps-deploy` stays the deploy branch (VPS checkout untouched — zero prod risk); delete `feat/cratemate-module` (verified ancestor of vps-deploy, fully contained).
- **D2 Old crate-mate:** archive the GitHub repo + HF Space redirect (was already plan 1b).
- **D3 Gemini/GCP:** DECOMMISSION (operator: "nevermind gemini") — remove Gemini fallback paths, revoke the GCP key (it was assumed leaked in the absorption punch list anyway).
- **D4 Auth docs:** replace the stale CF-Access doc with oauth2-proxy + KICK_TOKEN reality (T-005 proceeds).
- **D5 Standalone extension launch:** stays parked until the primary product stabilizes.

## Path forward

Resolve the CrateMate branch/canonical-branch decision first; it controls whether cleanup is a merge, archive, or delete.
Verify live production facts only on the VPS path described in `RUNTIME.md`; this Mac clone is editing context.
Patch stale auth docs immediately after verification so the old Cloudflare Access plan cannot guide future changes.
Use the tests and refactor/polish items as the autonomous-safe queue while the operator handles branch and credential decisions.
