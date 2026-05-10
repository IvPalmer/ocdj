# OCDJ Standalone — Launch Plan

**Strategic frame:** revive as a freemium **side bet** alongside SafeReply. Free tier matches Shovel; Pro tier (multi-platform) is what Shovel doesn't have. Realistic ceiling $500–2K MRR. Portfolio piece, not breadwinner.

---

## 1. Codebase audit

The standalone extension at `/Users/palmer/Work/Dev/ocdj/ocdj-helper-standalone/` is **80% ship-ready**. It is genuinely zero-backend — all storage in `chrome.storage.local`, no fetch to localhost, the only external HTTP calls are to `api.discogs.com` (videos), `soundcloud.com/oembed` (resolve embed), and the embed iframes themselves.

**What's built and working:**
- MV3 service worker (15 KB) — message routing, Discogs API fetch, dedup, queue management, side-panel notify, declarativeNetRequest rules to fix Referer for YT/SC embeds
- Content scripts for all 5 platforms (Discogs 16 KB is the heavyweight; Bandcamp 9 KB, YouTube 5 KB, SoundCloud 10 KB, Spotify 11 KB)
- `shared.js` button factory + toasts + JS-port of `parsers.py` video-title cleaner (artist/title split)
- Popup wantlist viewer with search, export `.txt`, remove items, clear-all
- Side panel queue player (22 KB `panel.js`) with multi-platform embeds, persistent state, next-track/next-release controls
- Options page: Discogs token + per-site toggles + toast toggle
- Icons at 16/48/128, injected CSS, MAIN-world script for SoundCloud `document.referrer` override

**What's missing for CWS launch:**
1. **Manifest leak** — `host_permissions` still lists `http://localhost:8002/*` and `http://127.0.0.1:8002/*`. **#1 CWS rejection trigger.** Strip both before submission.
2. **Import functionality** — README claims "import from file" but `popup.html`/`popup.js` only has Export and Clear. Either add the import handler or update the README.
3. **No paywall plumbing** — no ExtensionPay (or Stripe) integration. Free vs Pro feature gates do not exist.
4. **No privacy policy** — required by CWS even for local-only storage.
5. **No listing assets** — no screenshots, no promo tile, no demo video.
6. **No marketing site / landing page.**
7. **Manifest version** still `1.0.0` — fine for v1, but icon/branding polish wanted.

**Code quality:** clean, well-commented, idiomatic MV3. The Brazilian dev wrote tight code — `sendToBackground` Promise wrapper is good, dedup logic is sane (artist+title or source_url), MutationObserver debounce at 250ms, AbortSignal timeouts everywhere. **Shipping confidence: high.**

**Permissions audit (what reviewers will scrutinize):**
- `storage`, `activeTab`, `sidePanel`, `scripting` → all justified by the feature set
- `declarativeNetRequestWithHostAccess` → justified (Referer rewrite for embed iframes; explain in dashboard: "YouTube and SoundCloud reject embeds from chrome-extension origins")
- Host permissions: keep `api.discogs.com`, `soundcloud.com`, `w.soundcloud.com`, `api-v2.soundcloud.com`, `youtube.com`, `youtube-nocookie.com`. Remove the two localhost entries.

---

## 2. Free vs Pro feature split

| Feature | Free | Pro ($5/mo or $39 lifetime) |
|---|---|---|
| Discogs Wantlist button (release/track/label/artist/marketplace/search) | ✅ | ✅ |
| Discogs Queue + Side Panel YouTube playback | ✅ | ✅ |
| Wantlist export `.txt` | ✅ | ✅ |
| Discogs API token (higher rate limit) | ✅ | ✅ |
| **YouTube** Wantlist + Queue | Wantlist only | Wantlist + Queue |
| **SoundCloud** Wantlist + Queue | ❌ | ✅ |
| **Bandcamp** Wantlist + Queue | ❌ | ✅ |
| **Spotify** Wantlist + Queue | ❌ | ✅ |
| Wantlist import (file → list) | ❌ | ✅ |
| Cloud sync across devices (future v1.1) | ❌ | ✅ |
| Bulk-export to CSV/Rekordbox/Serato crate format (future) | ❌ | ✅ |

**Why this split works:** free tier is **deliberately Shovel-equivalent** — Discogs queue with YouTube playback. We don't undercut on Discogs because we can't (Shovel is already free + better-loved). We win the conversion the moment a user wants to queue a SoundCloud or Bandcamp track, which is *every* DJ workflow that goes beyond Discogs. Spotify previews and YouTube queue are bait — a free user discovers OCDJ on YouTube, hits a paywall on the queue button, that's the upsell moment.

---

## 3. Competitive positioning

| Competitor | Their strength | Our angle |
|---|---|---|
| **Shovel for Discogs** (free, 7K, 4.8★) | Discogs-native side panel + bulk queue 250+ items | We match free, beat them on multi-platform Pro |
| **Discogs Enhancer Pro** ($3/mo, 10K) | Filters, seller blocklist, marketplace tools | Different problem space — we coexist |
| **Discogify** (free, 562) | Preview + playlist + shortcuts | Limited DOM coverage, abandoned aesthetic |
| **Discogs Player** (free, 422) | Inline player + Play All | Stale, single-surface |
| **Discogs Notifier** (free, 287) | Telegram price alerts | Different problem (price tracking) |

**1★ pain points to mine** (from Shovel reviews): "Spotify links would be nice", "want SoundCloud support", "wish it worked on Bandcamp". **Every one of those is our Pro tier.**

**Our 1-line vs Shovel:** *"Shovel for everywhere — queue tracks across Discogs, YouTube, SoundCloud, Bandcamp, and Spotify, not just Discogs."*

**Tone:** explicitly NOT a competitor — colleagues. Reach out to Shovel devs on launch and offer cross-promo. Position as "the Pro version of the Discogs digging workflow you already love."

---

## 4. Technical readiness gaps

**Pre-submission checklist (Week 1):**
- [ ] Strip `localhost`/`127.0.0.1` lines from `manifest.json` host_permissions
- [ ] Add ExtensionPay (`extensionpay.com` — 1-day setup, takes 10% rev share, no Stripe Atlas needed). Wrap SC/BC/Spotify content scripts and YouTube queue handler in `await extpay.getUser().paid` checks; show paywall toast + side-panel CTA on free users
- [ ] Add wantlist import handler in popup (parse exported `.txt` back, dedup, write to storage)
- [ ] Privacy policy on GitHub Pages (template: "OCDJ stores wantlist and queue data locally in chrome.storage.local. We do not collect, transmit, or share any user data. Discogs API requests are made directly from your browser using your token if provided.")
- [ ] Permissions justification doc (saved as `docs/CWS_JUSTIFICATIONS.md`, copy each line into CWS dashboard at submission)
- [ ] `manifest.json` description bumped to ≤132 chars matching CWS short-description rules

**Listing assets (Week 2):**
- 5 screenshots @ 1280×800 PNG: (1) Discogs release page with buttons, (2) side-panel player with queue, (3) popup wantlist with search, (4) Bandcamp + SoundCloud showing Pro multi-platform, (5) settings + Pro upgrade screen
- 440×280 small promo tile (Figma quick mock)
- 1280×800 marquee tile (optional but boosts visibility)
- 30-second demo video — Loom or `pagecast` MCP, screen-record digging on Discogs → queue release → side panel plays → wantlist export. Caption-style overlay "Shovel for everywhere."
- Marketing site: single-page on Vercel at `ocdj.app` or `crate-mate.app` ($12/yr domain). Hero + "Pro vs Free" table + screenshots + Stripe-gated download CTA (or just CWS link). Reuse SafeReply landing page Tailwind boilerplate.
- Test on fresh Chrome profile (zero extensions, zero history) — verify no console errors, all 5 platforms inject buttons correctly, side panel persists across browser restart, paywall fires on free users

---

## 5. Marketing positioning + launch plan

**Tagline options:**
1. **"Shovel for everywhere."** ← winner
2. "One queue, every platform."
3. "The DJ digging extension for the rest of the web."

**Launch surfaces (Week 3):**
- **r/DJs** post: "I built a free Discogs queue tool — wanted multi-platform so I extended it. Free tier matches Shovel, paid unlocks SC/BC/Spotify." Honest, not slick. Tag u/Shovel-dev as colleagues.
- **r/Beatmatch, r/turntablism, r/vinyl, r/electronicmusic** — same post adapted, focus on the use case ("I lost my crate-digging momentum every time I bounced from Discogs to SoundCloud")
- **r/chrome_extensions** Show HN-style post (this audience values the technical zero-backend angle)
- **DJ TechTools forum** + **DJforums.com** "Software" boards — single launch thread each
- **Resident Advisor / DJ Mag tip line** — RA covered Shovel; pitch them "the multi-platform sequel". Email tips@ra.co with one-paragraph pitch + 30s demo
- **YouTube DJ-tool reviewers** — outreach to Crossfader, DJcityTV, Phil Morse / Digital DJ Tips, Mojaxx, Kolor Bass. Send free Pro lifetime keys + 60s demo. ~3% conversion to coverage.
- **Hashtag campaign** `#cratedigging` `#digitaldigging` `#vinylcommunity` on Instagram + TikTok. 5–10 short clips of "queue → side panel" magic moments.

**No Product Hunt** for launch — DJs don't live there, conversion is poor for niche tools, we'd compete with broader extensions for attention.

---

## 6. Revenue projections

| Horizon | Free installs | Conversion | MRR | Notes |
|---|---|---|---|---|
| 6 months | 800–2,500 | 1–2% | **$50–200** | r/DJs + Shovel cross-promo + 1 RA blurb optimistic case |
| 12 months | 3K–8K | 1.5–3% | **$200–800** | Compounding from review trickle, YouTube reviewer hits |
| 24 months | 8K–20K | 2–3% | **$500–2K** | Ceiling — DJ market is finite (20–50K paid-willing globally) |

Easy Folders ($3.7K MRR @ 6 mo post-launch) is precedent **but** that's the ChatGPT power-user audience (millions). DJs are a 100x smaller pool. Don't anchor on $3.7K — anchor on **Discogs Enhancer Pro at $3/mo** (different competitor, similar audience size, undisclosed revenue but probably $500–1.5K MRR after 5 years).

**Honest:** OCDJ will not pay rent. Best case it's $1K MRR in year 2, which is real money but not life-changing.

---

## 7. Build effort to ship

**3-week realistic timeline:**

**Week 1 — Code (5 dev days):**
- Day 1: strip localhost from manifest, test on fresh profile, fix any DOM-selector breakage
- Day 2: ExtensionPay integration + paywall gates on SC/BC/Spotify content scripts and YouTube queue handler
- Day 3: add wantlist import to popup, polish settings page Pro-tier reveal
- Day 4: privacy policy on GitHub Pages, permissions justification doc, manifest description polish
- Day 5: end-to-end testing fresh profile, fix bugs

**Week 2 — Assets (5 days):**
- Day 1–2: 5 screenshots + 1 promo tile (Figma) + Loom demo recording
- Day 3–4: marketing site on Vercel (`ocdj.app` reusing SafeReply boilerplate)
- Day 5: CWS submission (review takes 1–7 days)

**Week 3 — Launch:**
- Day 1: CWS approval (assume mid-week)
- Day 2: r/DJs + DJ forum posts
- Day 3: YouTube reviewer outreach (10 emails)
- Day 4: RA/DJ Mag pitch
- Day 5: monitor reviews, hotfix anything broken

**Total: 15 working days. 3 calendar weeks part-time. Compressible to 10 days if focused.**

---

## 8. Maintenance load post-launch

- **Selector breakage** — 5 platforms × ~quarterly DOM changes = ~20 incidents/year. Each is 30–90 min to patch + rebuild + republish. Budget **~30 hr/year** ongoing.
- **Customer support** — DJs are loud but cheap. Expect 5–15 emails/week at scale. Mostly "doesn't work on X" (selector breakage), refund requests (handle via ExtensionPay dashboard, generous policy), feature requests (politely decline outside scope).
- **Refund handling** — ExtensionPay handles via Stripe; budget 1–3% refund rate on Pro.
- **CWS policy changes** — annual MV3 / privacy-policy update. ~2 hr.

**Total ongoing: ~50–80 hr/year (roughly one weekend per month).**

---

## 9. Honest verdict

**Ship it AFTER SafeReply, not before.**

Reasoning:
- SafeReply is the thesis — primary product, larger TAM, real revenue ceiling. Don't dilute it for a 3-week side bet that maxes at $1K MRR.
- OCDJ's distribution channels (r/DJs, RA, DJ Mag) are **different audiences** from SafeReply (ChatGPT power-users, knowledge workers). No cross-promotion synergy.
- Shipping OCDJ first as "portfolio + traffic builder" sounds clever but the traffic doesn't transfer — DJs aren't your SafeReply audience.
- BUT: OCDJ is *80% built*, the marginal cost is low (~3 weeks), and the user has already paid the dev to build it. Sunk-cost says ship it eventually.

**Recommended sequence:**
1. **Now (May 2026)** → focus 100% on SafeReply. Don't touch OCDJ.
2. **SafeReply launch + 4 weeks stabilization** → if SafeReply has traction, take a 3-week break from it and ship OCDJ as a maintenance side product.
3. **If SafeReply stalls** → OCDJ becomes a useful "I shipped 2 things this year" portfolio piece + small recurring revenue.

The one scenario where you'd ship OCDJ first: if SafeReply hits a wall in the next 2 weeks (auth blockers, no LinkedIn signal) and you need a confidence-rebuilding ship. Otherwise, **OCDJ is the dessert, not the main course.**

---

## 10. Final spec stub

**Product name (3 options):**
1. **OCDJ** (keep — already built, has a `.zip` sitting at `/Users/palmer/Work/Dev/ocdj/ocdj-helper-standalone.zip`, brand has zero equity but zero baggage either)
2. **CrateMate** (better for marketing — DJ idiom, available `.app` domain at time of writing)
3. **Digger** (cleanest, but generic and likely SEO-impossible)

→ **Recommend rebranding to CrateMate** for CWS listing. "OCDJ" reads internal/cryptic to a stranger.

**Tagline:** *"Shovel for everywhere — queue tracks across Discogs, YouTube, SoundCloud, Bandcamp, and Spotify."*

**v1 Free tier:** Discogs full feature set + YouTube wantlist + side-panel YouTube playback. Matches Shovel exactly.

**v1 Pro tier ($5/mo or $39 lifetime):** SoundCloud + Bandcamp + Spotify wantlist & queue, YouTube queue, wantlist file import, future cloud-sync.

**Pricing:** $5/mo OR $39 lifetime. Lifetime conversion will dominate (DJs hate subs). Frame: "$39 once = 8 months of subscription."

**Launch plan:** 3 weeks (1 code, 1 assets, 1 launch). r/DJs primary. RA/DJ Mag secondary. YouTube reviewer outreach tertiary.

**Realistic 12-mo MRR:** $200–800.

**Kill criteria (revisit at month 6):**
- < 500 free installs by month 3 → distribution is dead, don't put in more time, leave it on CWS as portfolio piece
- < 5 paid users by month 6 → Pro features aren't differentiating enough, consider going fully free + sponsor model (Discogs affiliate?)
- > 5 selector breakages/month → maintenance load not worth $1K MRR ceiling, archive the project

---

**Bottom line:** Don't ship OCDJ now. Park this plan. After SafeReply ships and stabilizes, this becomes a focused 3-week side project with realistic $200–800 MRR upside. If SafeReply succeeds, OCDJ is dessert. If SafeReply fails, OCDJ is a respectable consolation ship.
