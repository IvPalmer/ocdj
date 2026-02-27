from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from .config import load_config
    from .local_inventory import mark_list_ids_seen, scan_local_traxdb
    from .pixeldrain import PixeldrainClient
    from .traxdb_scrape import scrape_pixeldrain_list_links, make_session
except ImportError:
    from config import load_config  # type: ignore[no-redef]
    from local_inventory import mark_list_ids_seen, scan_local_traxdb  # type: ignore[no-redef]
    from pixeldrain import PixeldrainClient  # type: ignore[no-redef]
    from traxdb_scrape import scrape_pixeldrain_list_links, make_session  # type: ignore[no-redef]


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Sync TraxDB -> Pixeldrain -> local traxdb folder")
    p.add_argument(
        "--config",
        default=None,
        help="Optional path to config JSON (by default loads traxdb_sync/config.json and ~/.config/traxdb_sync/config.json).",
    )
    p.add_argument(
        "--traxdb-root",
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "traxdb")),
        help="Local traxdb/ folder (default: ../traxdb)",
    )
    p.add_argument(
        "--traxdb-start-url",
        required=False,
        help="TraxDB (blogspot) page URL to start scraping, e.g. https://traxdb2.blogspot.com/search?updated-max=... (or set in config/env)",
    )
    p.add_argument(
        "--traxdb-cookies",
        default=None,
        help="Path to cookies file (cookies.txt Netscape or cookies.json) for authenticated scraping (or set in config/env)",
    )
    p.add_argument("--max-pages", type=int, default=50, help="How many pages to follow from the start URL")
    p.add_argument(
        "--stop-at-local-max-date",
        action="store_true",
        help="Stop scraping when we reach posts dated at-or-before your newest local traxdb date folder.",
    )
    p.add_argument(
        "--no-stop-at-local-max-date",
        dest="stop_at_local_max_date",
        action="store_false",
        help="Disable stop condition and scan all pages up to --max-pages.",
    )
    p.set_defaults(stop_at_local_max_date=True)
    p.add_argument(
        "--cutoff-date",
        default=None,
        help="Skip processing links whose inferred post date is <= this YYYY-MM-DD. Defaults to your newest local traxdb date folder.",
    )
    p.add_argument(
        "--no-cutoff-date",
        dest="use_cutoff_date",
        action="store_false",
        help="Disable cutoff-date filtering (process all links found, subject to list-id seen filtering).",
    )
    p.set_defaults(use_cutoff_date=True)

    p.add_argument(
        "--pixeldrain-api-key",
        default=None,
        help="Pixeldrain API key (or set PIXELDRAIN_API_KEY env var / config.json)",
    )
    p.add_argument(
        "--download",
        action="store_true",
        help="Actually download files. If omitted, runs in report-only mode.",
    )
    p.add_argument(
        "--plan-files",
        action="store_true",
        help="In report-only mode, also query Pixeldrain to include per-list file plans (slower; may error on dead links).",
    )
    p.add_argument(
        "--skip-existing-by-name",
        action="store_true",
        help="Skip downloading any FLAC whose basename already exists anywhere in local traxdb/ (fast dedupe).",
    )
    p.add_argument(
        "--report-path",
        default=None,
        help="Write a JSON report here (default: traxdb_sync_report_<timestamp>.json in cwd).",
    )

    args = p.parse_args(argv)

    cfg = load_config(args.config)
    traxdb_start_url = args.traxdb_start_url or cfg.traxdb_start_url
    traxdb_cookies = args.traxdb_cookies or cfg.traxdb_cookies
    pixeldrain_api_key = args.pixeldrain_api_key or cfg.pixeldrain_api_key

    if not traxdb_start_url:
        print("ERROR: Missing --traxdb-start-url (or set TRAXDB_START_URL / config.json)", file=sys.stderr)
        return 2

    inv = scan_local_traxdb(args.traxdb_root)

    cutoff_date = None
    if args.use_cutoff_date:
        cutoff_date = args.cutoff_date or inv.max_date_dir

    session = make_session(cookies_path=traxdb_cookies)
    links = scrape_pixeldrain_list_links(
        session,
        start_url=traxdb_start_url,
        max_pages=args.max_pages,
        stop_at_or_before_date=inv.max_date_dir if args.stop_at_local_max_date else None,
    )

    # filter out lists we have already processed before (optional state file)
    new_links = [l for l in links if l.list_id not in inv.list_ids_seen]
    skipped_by_cutoff = []
    if cutoff_date:
        kept = []
        for l in new_links:
            if l.inferred_date and l.inferred_date <= cutoff_date:
                skipped_by_cutoff.append(l)
            else:
                kept.append(l)
        new_links = kept

    if (args.download or args.plan_files) and not pixeldrain_api_key:
        print("ERROR: --download requires Pixeldrain API key (PIXELDRAIN_API_KEY or config.json)", file=sys.stderr)
        return 2

    client = PixeldrainClient(api_key=pixeldrain_api_key) if (args.download or args.plan_files) else None

    report: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "traxdb_root": inv.traxdb_root,
        "local_date_dirs": inv.date_dirs,
        "links_found": [l.__dict__ for l in links],
        "links_new": [l.__dict__ for l in new_links],
        "links_skipped_by_cutoff_date": [l.__dict__ for l in skipped_by_cutoff],
        "download_enabled": bool(args.download),
        "config_used": {
            "traxdb_start_url": traxdb_start_url,
            "traxdb_cookies": traxdb_cookies,
            "pixeldrain_api_key_present": bool(pixeldrain_api_key),
            "cutoff_date": cutoff_date,
        },
        "downloads": [],
        "errors": [],
    }

    # Decide where to put files:
    # - If we can infer a date from the page, put them in traxdb/<date>/
    # - Otherwise, put them in traxdb/_inbox/<list_id>/
    inbox_root = os.path.join(inv.traxdb_root, "_inbox")

    for l in new_links:
        try:
            if l.inferred_date:
                dest_dir = os.path.join(inv.traxdb_root, l.inferred_date)
            else:
                dest_dir = os.path.join(inbox_root, l.list_id)

            plan = []
            if client is not None and (args.download or args.plan_files):
                list_files = list(client.iter_list_files(l.list_id))
                for f in list_files:
                    skip = False
                    if args.skip_existing_by_name and f.name in inv.flac_basenames:
                        skip = True
                    plan.append(
                        {
                            "file_id": f.id,
                            "name": f.name,
                            "size": f.size,
                            "dest_dir": dest_dir,
                            "skip_reason": "already_exists_by_name" if skip else None,
                        }
                    )

            item = {
                "list_id": l.list_id,
                "pixeldrain_url": l.pixeldrain_url,
                "source_url": l.source_url,
                "inferred_date": l.inferred_date,
                "dest_dir": dest_dir,
                "files": plan,
                "download_results": [],
            }

            if args.download:
                os.makedirs(dest_dir, exist_ok=True)
                for pf in plan:
                    if pf["skip_reason"]:
                        item["download_results"].append(
                            {
                                "name": pf["name"],
                                "file_id": pf["file_id"],
                                "skipped": True,
                                "reason": pf["skip_reason"],
                            }
                        )
                        continue

                    dest_path = os.path.join(dest_dir, os.path.basename(pf["name"]))
                    downloaded, bytes_written = client.download_file(
                        pf["file_id"],
                        dest_path,
                        expected_size=pf["size"],
                        overwrite=False,
                        resume=True,
                    )
                    item["download_results"].append(
                        {
                            "name": pf["name"],
                            "file_id": pf["file_id"],
                            "dest_path": dest_path,
                            "downloaded": downloaded,
                            "bytes_written": bytes_written,
                        }
                    )

                # mark list seen only after successful processing
                mark_list_ids_seen(inv.traxdb_root, [l.list_id])

            report["downloads"].append(item)
        except Exception as e:
            report["errors"].append(
                {
                    "list_id": l.list_id,
                    "pixeldrain_url": l.pixeldrain_url,
                    "source_url": l.source_url,
                    "error": repr(e),
                }
            )

    report_path = args.report_path or os.path.abspath(f"traxdb_sync_report_{_now_stamp()}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"Wrote report: {report_path}")
    if report["errors"]:
        print(f"Errors: {len(report['errors'])} (see report)")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


