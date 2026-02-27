from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from .config import load_config
    from .local_inventory import mark_list_ids_seen, scan_local_traxdb
    from .pixeldrain import PixeldrainClient
except ImportError:
    from config import load_config  # type: ignore[no-redef]
    from local_inventory import mark_list_ids_seen, scan_local_traxdb  # type: ignore[no-redef]
    from pixeldrain import PixeldrainClient  # type: ignore[no-redef]


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _is_pixeldrain_not_found(e: Exception) -> bool:
    """
    Pixeldrain returns 404 for deleted/expired lists. Treat those as a 'dead link'
    instead of a fatal error, since there's nothing we can download.
    """
    msg = repr(e)
    return "(404)" in msg or '"value":"not_found"' in msg or "not_found" in msg


def _pick_dest_dir(traxdb_root: str, inferred_date: Optional[str], list_id: str) -> str:
    if inferred_date:
        return os.path.join(traxdb_root, inferred_date)
    return os.path.join(traxdb_root, "_inbox", list_id)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Download Pixeldrain lists listed in a sync report JSON")
    p.add_argument("--config", default=None, help="Optional config JSON path (same as sync.py)")
    p.add_argument(
        "--traxdb-root",
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "traxdb")),
        help="Local traxdb/ folder (default: ../traxdb)",
    )
    p.add_argument("--report", required=True, help="Sync report JSON with links_found/links_new")
    p.add_argument(
        "--links-key",
        default="links_found",
        help="Which key to read lists from: links_found or links_new (default: links_found)",
    )
    p.add_argument(
        "--overwrite-mismatch",
        action="store_true",
        help="If a destination file exists but has the wrong size, overwrite it (backup is still kept).",
    )
    p.add_argument(
        "--no-overwrite-mismatch",
        dest="overwrite_mismatch",
        action="store_false",
        help="Do not overwrite mismatches; keep original and download a side-by-side *.pixeldrain file.",
    )
    p.add_argument(
        "--backup-dir",
        default=None,
        help="Where to store backups of mismatched files (default: <traxdb_root>/_mismatch_backups/<timestamp>/)",
    )
    p.add_argument(
        "--report-path",
        default=None,
        help="Write a download report JSON here (default: traxdb_download_report_<timestamp>.json in cwd).",
    )
    p.add_argument(
        "--progress-path",
        default=None,
        help="Write a live progress JSON file here (updated frequently).",
    )
    p.add_argument(
        "--progress-interval-s",
        type=int,
        default=10,
        help="How often to update the live progress file (seconds).",
    )
    p.add_argument(
        "--lock-path",
        default=None,
        help="Lock file path to prevent concurrent runs (default: <traxdb_root>/.download_from_report.lock).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce console output (progress file still updates).",
    )
    p.add_argument(
        "--dedupe-identical-lists",
        action="store_true",
        help="If two Pixeldrain lists have identical (name,size) contents, only download the first and skip the rest (mirror dedupe).",
    )
    p.add_argument(
        "--no-dedupe-identical-lists",
        dest="dedupe_identical_lists",
        action="store_false",
        help="Disable mirror dedupe; process all lists from the report.",
    )
    p.set_defaults(dedupe_identical_lists=True)
    p.set_defaults(overwrite_mismatch=True)
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    if not cfg.pixeldrain_api_key:
        raise SystemExit("Missing Pixeldrain API key (PIXELDRAIN_API_KEY or config.json)")

    inv = scan_local_traxdb(args.traxdb_root)
    client = PixeldrainClient(api_key=cfg.pixeldrain_api_key)

    lock_path = args.lock_path or os.path.join(inv.traxdb_root, ".download_from_report.lock")
    # Simple lock: atomic create. If lock exists, refuse to run.
    # (If you crash, delete the lock file manually.)
    lock_fd = None
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, f"pid={os.getpid()}\nstarted_at={datetime.now().isoformat()}\n".encode("utf-8"))
    except FileExistsError:
        raise SystemExit(f"Another download appears to be running (lock exists): {lock_path}")

    try:
        with open(args.report, "r", encoding="utf-8") as f:
            sr = json.load(f)

        links = sr.get(args.links_key) or []
        if not isinstance(links, list):
            raise SystemExit(f"Report key {args.links_key} is not a list")

        backup_root = args.backup_dir or os.path.join(inv.traxdb_root, "_mismatch_backups", _now_stamp())
        backup_root_created = False

        started_at = datetime.now().isoformat()
        out: Dict[str, Any] = {
            "generated_at": datetime.now().isoformat(),
            "traxdb_root": inv.traxdb_root,
            "source_report": os.path.abspath(args.report),
            "links_key": args.links_key,
            "backup_root": backup_root,
            "overwrite_mismatch": bool(args.overwrite_mismatch),
            "lists": [],
            "dead_links": [],
            "errors": [],
            "summary": {
                "started_at": started_at,
                "lists_total": 0,
                "lists_completed": 0,
                "lists_dead": 0,
                "files_total": 0,
                "files_completed": 0,
                "bytes_total": 0,
                "bytes_downloaded": 0,
            },
        }

        # Pre-compute totals for better progress/ETA and cache list files to
        # avoid fetching each list twice (best-effort; list API calls may fail).
        total_lists = 0
        total_files = 0
        total_bytes = 0
        signature_seen: Dict[tuple, str] = {}
        files_cache: Dict[str, list] = {}  # list_id -> list of PixeldrainFile
        for l in links:
            list_id = str(l.get("list_id") or "").strip()
            if not list_id:
                continue
            total_lists += 1
            try:
                files = list(client.iter_list_files(list_id))
                files_cache[list_id] = files
                if args.dedupe_identical_lists:
                    sig = tuple(sorted((pf.name, pf.size) for pf in files))
                    if sig in signature_seen:
                        # don't count duplicates in totals
                        total_lists -= 1
                        continue
                    signature_seen[sig] = list_id
                total_files += len(files)
                for pf in files:
                    if isinstance(pf.size, int):
                        total_bytes += pf.size
            except Exception:
                # don't fail early; we'll record real errors during download
                pass
        out["summary"]["lists_total"] = total_lists
        out["summary"]["files_total"] = total_files
        out["summary"]["bytes_total"] = total_bytes

        progress_path = args.progress_path or os.path.abspath("traxdb_download_progress.json")
        last_progress_write = 0.0

        def write_progress(force: bool = False) -> None:
            nonlocal last_progress_write
            now = time.time()
            if not force and now - last_progress_write < max(1, args.progress_interval_s):
                return
            last_progress_write = now
            tmp = progress_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)
            os.replace(tmp, progress_path)

        write_progress(force=True)

        if not args.quiet:
            print(
                f"Starting download: lists={total_lists} files~={total_files} bytes~={total_bytes/1024**3:.2f}GB",
                flush=True,
            )
            print(f"Progress file: {progress_path}", flush=True)

        downloaded_signatures: Dict[tuple, str] = {}

        for l in links:
            list_id = str(l.get("list_id") or "").strip()
            if not list_id:
                continue
            inferred_date = l.get("inferred_date")
            dest_dir = _pick_dest_dir(inv.traxdb_root, inferred_date, list_id)

            try:
                files = files_cache.get(list_id) or list(client.iter_list_files(list_id))
                if args.dedupe_identical_lists:
                    sig = tuple(sorted((pf.name, pf.size) for pf in files))
                    if sig in downloaded_signatures:
                        canonical = downloaded_signatures[sig]
                        if not args.quiet:
                            print(f"[list skip] {list_id} is identical to {canonical} (mirror duplicate)", flush=True)
                        out["lists"].append(
                            {
                                "list_id": list_id,
                                "pixeldrain_url": l.get("pixeldrain_url"),
                                "inferred_date": inferred_date,
                                "dest_dir": dest_dir,
                                "skipped": True,
                                "skip_reason": "identical_to_other_list",
                                "identical_to": canonical,
                                "files": [],
                            }
                        )
                        mark_list_ids_seen(inv.traxdb_root, [list_id])
                        write_progress(force=True)
                        continue
                    downloaded_signatures[sig] = list_id

                # Only create the destination folder once we know the list exists and
                # we're actually going to process it. This avoids leaving lots of empty
                # folders for dead/duplicate lists.
                os.makedirs(dest_dir, exist_ok=True)

                if not args.quiet:
                    print(
                        f"[list {out['summary']['lists_completed']+1}/{total_lists}] {list_id} -> {dest_dir} ({len(files)} files)",
                        flush=True,
                    )
                item = {
                    "list_id": list_id,
                    "pixeldrain_url": l.get("pixeldrain_url"),
                    "inferred_date": inferred_date,
                    "dest_dir": dest_dir,
                    "files": [],
                }

                for pf in files:
                    dest_path = os.path.join(dest_dir, os.path.basename(pf.name))
                    status = "downloaded"

                    # If file exists and size mismatches, back it up before we touch it
                    if os.path.exists(dest_path) and pf.size is not None:
                        existing_size = os.path.getsize(dest_path)
                        if existing_size != pf.size:
                            if not backup_root_created:
                                os.makedirs(backup_root, exist_ok=True)
                                backup_root_created = True
                            backup_path = os.path.join(backup_root, f"{list_id}__{os.path.basename(pf.name)}")
                            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                            shutil.copy2(dest_path, backup_path)

                            if args.overwrite_mismatch:
                                # start over cleanly
                                os.remove(dest_path)
                            else:
                                # keep original, download side-by-side
                                dest_path = dest_path + ".pixeldrain"

                            status = "mismatch_backed_up"

                    downloaded, bytes_written = client.download_file(
                        pf.id,
                        dest_path,
                        expected_size=pf.size,
                        overwrite=False,
                        resume=True,
                    )
                    out["summary"]["files_completed"] += 1
                    out["summary"]["bytes_downloaded"] += int(bytes_written)
                    write_progress(force=False)

                    item["files"].append(
                        {
                            "name": pf.name,
                            "file_id": pf.id,
                            "expected_size": pf.size,
                            "dest_path": dest_path,
                            "downloaded": downloaded,
                            "bytes_written": bytes_written,
                            "status": status,
                        }
                    )

                out["lists"].append(item)
                # mark as processed
                mark_list_ids_seen(inv.traxdb_root, [list_id])
                out["summary"]["lists_completed"] += 1
                write_progress(force=True)
            except Exception as e:
                if _is_pixeldrain_not_found(e):
                    out["dead_links"].append(
                        {
                            "list_id": list_id,
                            "pixeldrain_url": l.get("pixeldrain_url"),
                            "inferred_date": inferred_date,
                            "source_url": l.get("source_url"),
                            "error": repr(e),
                        }
                    )
                    out["summary"]["lists_dead"] += 1
                else:
                    out["errors"].append({"list_id": list_id, "error": repr(e)})
                write_progress(force=True)

        report_path = args.report_path or os.path.abspath(f"traxdb_download_report_{_now_stamp()}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)

        write_progress(force=True)
        if not args.quiet:
            print(f"Wrote download report: {report_path}", flush=True)
        if out["dead_links"] and not args.quiet:
            print(f"Dead links (Pixeldrain 404): {len(out['dead_links'])} (see report)", flush=True)
        if out["errors"]:
            print(f"Errors: {len(out['errors'])} (see report)", flush=True)
            return 2
        return 0
    finally:
        try:
            if lock_fd is not None:
                os.close(lock_fd)
        finally:
            try:
                os.remove(lock_path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())


