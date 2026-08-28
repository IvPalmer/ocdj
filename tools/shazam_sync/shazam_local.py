#!/usr/bin/env python3
"""Shazam feed — runs on the Mac, reads the playlist Shazam syncs into Music.

Why this shape, since it looks like a detour: Apple exposes no way to read your
own Shazam library. Verified, not assumed —

  * the macOS Shazam app is App Store build 2.11 from Aug 2022, has no Apple ID
    sign-in, and its Core Data store sits empty;
  * `com.apple.shazamd` syncs the library over CloudKit but materialises no
    readable file on disk (searched with Full Disk Access);
  * shazam.com/myshazam is a 404;
  * there is no history API — shazamio and friends only recognise audio;
  * Shortcuts, on macOS *and* iOS, ships exactly two Shazam actions ("Shazam
    It" and "Get details"), neither of which reads the library.

What Shazam does offer is syncing each identification into a streaming
service's playlist. So the library reaches this Mac as an Apple Music playlist,
Music.app holds it locally, and AppleScript can read it. That covers every
device — iPhone, Watch, Control Center, Siri — because it is the library sync
doing the writing, not any one app.

The known cost: only tracks Apple Music has in catalogue arrive. Promos and
white labels do not. Nothing available today fixes that; running the Spotify
sync in parallel widens the union of catalogues but never closes it.

Config: ~/.config/ocdj/traxdb-local.env — deliberately the same file the TraxDB
daemon uses. It already holds the one bearer token the VPS accepts, and a
second copy of a credential on this Mac is exactly what left every Pixeldrain
download 401ing for two weeks.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, List

import requests

CONFIG_PATH = os.path.expanduser("~/.config/ocdj/traxdb-local.env")
SEEN_PATH = os.path.expanduser("~/.config/ocdj/shazam-seen.json")

# Music.app localises playlist names, so match on any of them rather than
# making the operator rename a playlist Shazam recreates on its own.
PLAYLIST_NAMES = ["Faixas do Shazam", "My Shazam Tracks", "Mis temas de Shazam"]

# ASCII unit/record separators: track titles contain commas, pipes, dashes and
# quotes, and AppleScript's default list output would be ambiguous for all of
# them.
UNIT = "\x1f"
REC = "\x1e"


def log(msg: str) -> None:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} [shazam-local] {msg}", flush=True)


def load_config() -> Dict[str, str]:
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(f"config missing: {CONFIG_PATH}")
    cfg: Dict[str, str] = {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    if not cfg.get("TRAXDB_TOKEN") or not cfg.get("TRAXDB_API_URL"):
        raise SystemExit(f"config {CONFIG_PATH} needs TRAXDB_API_URL and TRAXDB_TOKEN")
    return cfg


def ingest_url(cfg: Dict[str, str]) -> str:
    """Derive the ingest endpoint from the TraxDB base URL.

    Same host, same token, different app — deriving it beats storing a second
    URL that can drift out of step with the first.
    """
    base = cfg["TRAXDB_API_URL"].rstrip("/")
    if base.endswith("/traxdb"):
        base = base[: -len("/traxdb")]
    return f"{base}/wanted/shazam/ingest/"


def read_playlist() -> List[dict]:
    """Pull the Shazam playlist out of Music.app.

    `persistent ID` is Music's own stable identifier for the track, which makes
    a reliable cursor: the playlist hands back everything on every read, and
    titles are far too mutable to key on.
    """
    names = ", ".join(f'"{n}"' for n in PLAYLIST_NAMES)
    script = f'''
    tell application "Music"
        set wanted to {{{names}}}
        set found to missing value
        repeat with n in wanted
            if (exists playlist (n as text)) then
                set found to playlist (n as text)
                exit repeat
            end if
        end repeat
        if found is missing value then return ""
        set out to ""
        repeat with t in (tracks of found)
            set out to out & (get persistent ID of t) & "{UNIT}" & ¬
                (get artist of t) & "{UNIT}" & (get name of t) & "{UNIT}" & ¬
                (get album of t) & "{UNIT}" & ((get date added of t) as text) & "{REC}"
        end repeat
        return out
    end tell
    '''
    proc = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(f"osascript failed: {proc.stderr.strip()[:300]}")

    rows = []
    for rec in proc.stdout.split(REC):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split(UNIT)
        if len(parts) < 5:
            continue
        pid, artist, title, album, added = parts[:5]
        rows.append({
            "external_id": pid,
            "artist": artist,
            "title": title,
            "album": album,
            "shazamed_at": added,
            "device": "Shazam → Apple Music",
        })
    return rows


def load_seen() -> set:
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {str(x) for x in data} if isinstance(data, list) else set()
    except Exception:
        return set()


def save_seen(ids: set) -> None:
    tmp = SEEN_PATH + ".tmp"
    os.makedirs(os.path.dirname(SEEN_PATH), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, indent=2)
    os.replace(tmp, SEEN_PATH)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true",
                    help="send every track, not just ones not seen before")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be sent and exit")
    ap.add_argument("--seed", action="store_true",
                    help="mark everything currently in the playlist as already "
                         "sent, without sending it — the switch-on move when "
                         "you want the feed from now on, not the backlog")
    args = ap.parse_args()

    cfg = load_config()
    rows = read_playlist()
    if not rows:
        log("Shazam playlist not found in Music.app (or empty) — nothing to do")
        return 0

    if args.seed:
        # Turning the feed on should not dump years of history into Wanted.
        # iCloud backfills the whole library into this playlist the moment the
        # Apple Music sync is enabled, so without this the first run would
        # ingest all of it.
        ids = {r["external_id"] for r in rows}
        save_seen(load_seen() | ids)
        log(f"seeded: {len(ids)} existing track(s) marked as already handled; "
            f"only new Shazams from here on")
        return 0

    seen = set() if args.all else load_seen()
    fresh = [r for r in rows if r["external_id"] not in seen]
    log(f"playlist has {len(rows)} track(s), {len(fresh)} not sent before")

    if args.dry_run:
        for r in fresh[:20]:
            log(f"  would send: {r['artist']} — {r['title']}")
        return 0

    # Post even when `fresh` is empty. The server treats the call as a
    # heartbeat, which is what lets the panel tell "nothing Shazamed lately"
    # apart from "this LaunchAgent has been dead for a week".

    resp = requests.post(
        ingest_url(cfg),
        headers={"Authorization": f"Bearer {cfg['TRAXDB_TOKEN']}"},
        json={"items": fresh},
        timeout=120,
    )
    resp.raise_for_status()
    body = resp.json()
    log(f"ingested: {body.get('created_count', 0)} new, "
        f"{body.get('skipped', 0)} already in Wanted")

    # Mark seen only after the POST lands: a failed request must be retried on
    # the next cycle, not silently dropped.
    save_seen(seen | {r["external_id"] for r in fresh})
    return 0


if __name__ == "__main__":
    sys.exit(main())
