#!/usr/bin/env python3
"""TraxDB local-download daemon — runs on the Mac, pulls straight from Pixeldrain.

The VPS scrapes the blog and tracks status; it never stores audio. This daemon
is the piece that actually fetches, so the archive only ever lands on the Mac.

Each cycle:
  1. POST /api/traxdb/local/inventory/  — report which date folders we hold
  2. POST /api/traxdb/local/claim/      — lease up to --limit lists
  3. download each list's files from Pixeldrain into <root>/<date>/
  4. POST .../complete/ or .../fail/

Safety rule that outranks everything: a date folder is atomic. If it exists
locally — even empty — we never write into it. The operator prunes individual
tracks out of folders by hand, and merging would silently restore them.
Downloads go to a sibling `.part` directory, renamed into place only once
complete, so an interrupted run never leaves something that looks like a real
folder.

Config: ~/.config/ocdj/traxdb-local.env
    TRAXDB_API_URL=https://ocdj.grooveops.dev/api/traxdb
    TRAXDB_TOKEN=<same value as DRAIN_TOKEN on the VPS>
    TRAXDB_LOCAL_ROOT=/Users/palmer/Music/Musicas/Electronic/ID3/traxdb
    PIXELDRAIN_API_KEY=<key>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from typing import Dict, List

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from traxdb_sync.pixeldrain import PixeldrainClient  # noqa: E402

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CONFIG_PATH = os.path.expanduser("~/.config/ocdj/traxdb-local.env")


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} [traxdb-local] {msg}", flush=True)


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
            cfg[k.strip()] = v.strip().strip('"').strip("'")

    missing = [k for k in ("TRAXDB_API_URL", "TRAXDB_TOKEN", "TRAXDB_LOCAL_ROOT") if not cfg.get(k)]
    if missing:
        raise SystemExit(f"config {CONFIG_PATH} missing keys: {', '.join(missing)}")
    return cfg


def local_date_dirs(root: str) -> List[str]:
    """Every date folder that exists, empty ones included.

    Emptiness is not a reason to refill: the operator prunes tracks out of a
    folder by hand, and deleting all of them is them saying they want nothing
    from that date. Reporting only non-empty folders would let the daemon
    restore it. Partial downloads live in `<date>.part`, which doesn't match
    the date pattern, so an interrupted run never looks like a real folder.
    """
    out = []
    try:
        for entry in os.scandir(root):
            if entry.is_dir() and DATE_DIR_RE.match(entry.name):
                out.append(entry.name)
    except FileNotFoundError:
        pass
    return sorted(out)


def safe_name(filename: str) -> str:
    """Reduce a Pixeldrain-supplied filename to a bare, safe basename.

    The names come from a third party. Left alone, `../2026-07-15/x.flac` would
    escape staging and write straight into a folder we promised never to touch.
    """
    name = os.path.basename(str(filename or '').strip().replace('\\', '/'))
    if not name or name in ('.', '..') or name.startswith('/'):
        raise ValueError(f'unsafe filename from Pixeldrain: {filename!r}')
    return name


class API:
    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}"})

    def report_inventory(self, date_dirs: List[str]) -> int:
        r = self.s.post(f"{self.base}/local/inventory/", json={"date_dirs": date_dirs},
                        timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("count", 0)

    def claim(self, limit: int) -> List[dict]:
        r = self.s.post(f"{self.base}/local/claim/", json={"limit": limit}, timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("items", [])

    def complete(self, pk: int, claim_token: str, files: List[dict]) -> None:
        r = self.s.post(f"{self.base}/local/{pk}/complete/",
                        json={"claim_token": claim_token, "files": files},
                        timeout=self.timeout)
        r.raise_for_status()

    def fail(self, pk: int, claim_token: str, reason: str) -> None:
        try:
            self.s.post(f"{self.base}/local/{pk}/fail/",
                        json={"claim_token": claim_token, "reason": reason},
                        timeout=self.timeout)
        except requests.RequestException as e:
            log(f"could not report failure for {pk}: {e!r}")


# ── Durable receipts ──────────────────────────────────────────
#
# Files land on disk before the VPS is told about it. If that POST fails, the
# folder is stuck 'downloading' forever: the date is now in our inventory, so
# the server will never offer it again and no retry would ever happen. Persist
# the receipt and replay it at the start of every cycle instead.

def receipts_dir(root: str) -> str:
    return os.path.join(root, "_reports", "pending-receipts")


def save_receipt(root: str, pk: int, claim_token: str, files: List[dict]) -> None:
    d = receipts_dir(root)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f"{pk}.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"id": pk, "claim_token": claim_token, "files": files}, f)
    os.replace(tmp, os.path.join(d, f"{pk}.json"))


def flush_receipts(api: "API", root: str) -> int:
    d = receipts_dir(root)
    if not os.path.isdir(d):
        return 0
    flushed = 0
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                rec = json.load(f)
            api.complete(rec["id"], rec.get("claim_token", ""), rec.get("files", []))
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code in (404, 409):
                # Folder is gone, already completed, or our lease was
                # reassigned — nothing left to report. Drop the receipt.
                os.remove(path)
                log(f"receipt {name} dropped (server said {code})")
            else:
                log(f"receipt {name} retry failed: {e!r}")
            continue
        except requests.RequestException as e:
            log(f"receipt {name} retry failed: {e!r}")
            continue
        except (OSError, ValueError, KeyError) as e:
            log(f"receipt {name} unreadable, dropping: {e!r}")
            os.remove(path)
            continue
        os.remove(path)
        flushed += 1
    if flushed:
        log(f"flushed {flushed} pending receipt(s)")
    return flushed


def download_one(client: PixeldrainClient, item: dict, root: str) -> List[dict]:
    """Fetch one list into <root>/<date>/. Returns the per-file records to report.

    Raises on any failure — the caller marks the list failed. Partial output is
    cleaned up so a retry starts from a clean slate rather than half a folder.
    """
    date = (item.get("inferred_date") or "").strip()
    dest = os.path.join(root, date) if DATE_DIR_RE.match(date) else \
        os.path.join(root, "_inbox", item["folder_id"])

    # ANY existing destination is off limits, empty included — see
    # local_date_dirs. The server filters these out too; this is the backstop
    # that makes the rule hold even if the inventory is stale.
    if os.path.exists(dest):
        raise RuntimeError(f"refusing to write into existing folder: {dest}")

    staging = dest + ".part"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    staging_real = os.path.realpath(staging)

    records = []
    seen_names = set()
    try:
        for track in item.get("tracks", []):
            file_id = track.get("pixeldrain_file_id")
            if not file_id:
                continue
            name = safe_name(track.get("filename"))
            if name in seen_names:
                raise RuntimeError(f"duplicate filename in list: {name!r}")
            seen_names.add(name)

            tmp_path = os.path.join(staging, name)
            # Belt and braces after safe_name: the written path must resolve
            # inside staging.
            if os.path.commonpath([staging_real, os.path.realpath(os.path.dirname(tmp_path))]) \
                    != staging_real:
                raise RuntimeError(f"path escapes staging: {name!r}")

            client.download_file(file_id, tmp_path, expected_size=track.get("size"))
            records.append({
                "pixeldrain_file_id": file_id,
                "local_path": os.path.join(dest, name),
                "bytes": os.path.getsize(tmp_path),
            })

        if not records:
            raise RuntimeError("list had no downloadable files")

        os.makedirs(os.path.dirname(dest) or root, exist_ok=True)
        # os.rename onto an existing empty dir would succeed and silently
        # replace it, so the existence check above is what protects us.
        os.rename(staging, dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return records


def run_cycle(cfg: Dict[str, str], limit: int) -> int:
    root = cfg["TRAXDB_LOCAL_ROOT"]
    if not os.path.isdir(root):
        raise SystemExit(f"TRAXDB_LOCAL_ROOT does not exist: {root}")

    api = API(cfg["TRAXDB_API_URL"], cfg["TRAXDB_TOKEN"])
    client = PixeldrainClient(api_key=cfg.get("PIXELDRAIN_API_KEY") or None)

    # Settle unfinished business before taking on more.
    flush_receipts(api, root)

    dirs = local_date_dirs(root)
    api.report_inventory(dirs)
    log(f"reported {len(dirs)} local date folders (newest {dirs[-1] if dirs else 'none'})")

    items = api.claim(limit)
    if not items:
        log("nothing pending")
        return 0

    log(f"claimed {len(items)} list(s)")
    done = 0
    for item in items:
        label = f"{item['folder_id']} ({item.get('inferred_date') or 'no date'})"
        token = item.get("claim_token", "")
        try:
            records = download_one(client, item, root)
        except Exception as e:
            log(f"FAILED {label}: {e!r}")
            api.fail(item["id"], token, repr(e)[:400])
            continue

        # Write the receipt before reporting: if the POST or the process dies
        # here, the next cycle replays it instead of stranding the folder.
        save_receipt(root, item["id"], token, records)
        try:
            api.complete(item["id"], token, records)
        except requests.RequestException as e:
            log(f"downloaded {label} but confirm failed, receipt saved: {e!r}")
            continue
        try:
            os.remove(os.path.join(receipts_dir(root), f"{item['id']}.json"))
        except OSError:
            pass
        log(f"OK {label}: {len(records)} files")
        done += 1

    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=3, help="lists to claim per cycle")
    ap.add_argument("--inventory-only", action="store_true",
                    help="report local folders and exit (no downloading)")
    args = ap.parse_args()

    cfg = load_config()

    if args.inventory_only:
        api = API(cfg["TRAXDB_API_URL"], cfg["TRAXDB_TOKEN"])
        dirs = local_date_dirs(cfg["TRAXDB_LOCAL_ROOT"])
        log(f"reported {api.report_inventory(dirs)} date folders")
        return 0

    try:
        run_cycle(cfg, args.limit)
    except requests.RequestException as e:
        log(f"API unreachable: {e!r}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
