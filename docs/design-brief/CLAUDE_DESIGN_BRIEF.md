# OCDJ — Claude Design Brief

Paste this into Claude Design's chat after clicking **Set up design system** → **Describe the system** (or into a new Prototype's initial prompt).

---

## What OCDJ is

OCDJ is a personal DJ tooling app that automates the full track-acquisition workflow for a working DJ. One user, power-user by design, runs it locally. Stack: Django + React (Vite) + Postgres + slskd + Docker.

## Mental model (the spine)

Every feature maps to one of five workflow stages. This should be visible from every screen — it's the product's backbone, not just a nav grouping.

1. **Capture** — Recognize (ID tracks in mixes via ACRCloud + Shazam + TrackID), TraxDB (scrape blogs)
2. **Curate** — Wanted (wishlist across Spotify / SoundCloud / YouTube / Discogs)
3. **Fetch** — Soulseek (slskd P2P search + download)
4. **Process** — Organize (9-stage pipeline: downloaded → tagged → renamed → converted → ready)
5. **Library** — final iTunes-bound tracks + Settings

Agent (chat sidecar) and Dashboard sit outside the spine as meta-tools.

## Who it's for

One DJ. Power user. Running locally beside Ableton / Serato. Uses it late at night. Values:
- Density over decoration — wants live signal (queue depth, running jobs, recent finds), not empty canvases
- Keyboard-driven workflows
- Seeing pipeline state at a glance
- Dark mode by default

## Current design problems (from audit)

1. Every panel wastes 60–70% of the canvas with empty space
2. Connection / health state rendered inconsistently — chip on Soulseek, pills on Dashboard, text on Settings
3. Organize's 9-stage pipeline overflows horizontally, crops on desktop
4. The Capture→Curate→Fetch→Process→Library spine is rendered as faint 10px gray labels — demoted
5. Three button styles with no system (solid blue, solid black, ghost)
6. No dark mode, no accent color, no visual identity for a DJ tool
7. Mobile: sidebar overflows, content clips
8. Dashboard shows only stat cards (all zeros) — no activity, no context

## Design direction

**Dark-first.** Neutral grays + one saturated accent (cyan or magenta — DJ feel, evokes CDJ / Serato). Paper-white variant optional.

**Global workspace shell:**
- Top strip = live status (Backend / slskd / sidecar dots, queue count, current job)
- Left rail = workflow stages as primary nav with icons + counts, spine always visible
- Main = context panel for active stage
- Right rail = activity feed (running recognize, in-flight downloads, recent finds)

**Dashboard = command center**, not a stat grid. Activity stream + queue snapshot + last-completed + top CTA to next pending work.

**Organize pipeline regrouped** into 4 expandable phases: Ingest → Name → Convert → Ship. Vertical rail preferred over a 9-column horizontal strip.

**Componentize**: one button system (primary/secondary/ghost/danger), one status-pill component, one empty-state template (icon + headline + next-step buttons — Wanted already does this right).

## Existing design tokens (start here, extend, don't replace)

```css
--bg: #ffffff
--bg-raised: #f8f9fa
--bg-surface: #f1f3f5
--border: #e1e4e8
--text: #1a1d21
--text-secondary: #4a5568
--text-muted: #8b95a5
--accent-blue: #2563eb
--accent-green: #16a34a
--accent-amber: #d97706
--accent-red: #dc2626
--accent-violet: #7c3aed
--radius: 8px
--font: Inter
--mono: SF Mono / Fira Code
```

Derive dark equivalents + add one brand accent.

## Component inventory (existing React components)

- `Layout` (sidebar + main)
- `Dashboard` (stat grid + service status)
- `WantedList`, `ImportPanel`, `ImportPreview`, `AddItemForm`
- `SoulseekPanel`, `BrowseModal`
- `TraxDBPanel`
- `RecognizePanel`
- `OrganizePanel` (9-stage pipeline)
- `LibraryPanel`
- `AgentPanel` (chat)
- `SettingsPanel`
- `ErrorBoundary`, `Toast`

## Tech constraints

- React 18 + Vite + React Router
- CSS variables (no Tailwind, no CSS-in-JS) — match existing approach
- TanStack Query for data, TanStack Table for tables
- Must stay accessible at 1280px wide (primary), degrade gracefully below

## What I want back

Priority order:
1. Global shell: sidebar + top status strip + right activity rail (dark mode)
2. Dashboard redesign (activity-first)
3. Organize pipeline regroup (4 phases, vertical or condensed)
4. Standardized button / status-pill / empty-state components
5. Dark mode token set

Export target: HTML/CSS that I can translate into the existing React + CSS-variable structure.
