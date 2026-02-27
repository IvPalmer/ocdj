from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from .config import load_config
    from .local_inventory import scan_local_traxdb
    from .pixeldrain import PixeldrainClient
except ImportError:
    from config import load_config  # type: ignore[no-redef]
    from local_inventory import scan_local_traxdb  # type: ignore[no-redef]
    from pixeldrain import PixeldrainClient  # type: ignore[no-redef]


@dataclass(frozen=True)
class LocalFileHit:
    path: str
    size: int


def _build_name_index(traxdb_root: str) -> Dict[str, List[LocalFileHit]]:
    """
    Map basename -> list of (path,size) hits across traxdb_root.
    We include *.flac plus anything else we might download (wav/aiff), but prioritize exact sizes.
    """
    idx: Dict[str, List[LocalFileHit]] = {}
    for base, _, files in os.walk(traxdb_root):
        for name in files:
            if name.startswith("."):
                continue
            # keep it simple: index all files, not only flac
            p = os.path.join(base, name)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            idx.setdefault(name, []).append(LocalFileHit(path=p, size=sz))
    return idx


def _pick_dest_dir(traxdb_root: str, inferred_date: Optional[str], list_id: str) -> str:
    if inferred_date:
        return os.path.join(traxdb_root, inferred_date)
    return os.path.join(traxdb_root, "_inbox", list_id)


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _is_pixeldrain_not_found(e: Exception) -> bool:
    msg = repr(e)
    return "(404)" in msg or '"value":"not_found"' in msg or "not_found" in msg


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Audit local traxdb files against Pixeldrain list contents")
    p.add_argument("--config", default=None, help="Optional config JSON path (same as sync.py)")
    p.add_argument(
        "--traxdb-root",
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "traxdb")),
        help="Local traxdb/ folder (default: ../traxdb)",
    )
    p.add_argument(
        "--report",
        required=True,
        help="A sync report JSON which contains links_found/links_new with list_id and inferred_date",
    )
    p.add_argument(
        "--global-search-by-name",
        action="store_true",
        help="If a file isn't found in the expected destination folder, search anywhere in traxdb/ by basename.",
    )
    p.add_argument(
        "--report-path",
        default=None,
        help="Write audit report JSON here (default: traxdb_audit_report_<timestamp>.json in cwd).",
    )
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    if not cfg.pixeldrain_api_key:
        raise SystemExit("Missing Pixeldrain API key (PIXELDRAIN_API_KEY or config.json)")

    inv = scan_local_traxdb(args.traxdb_root)
    client = PixeldrainClient(api_key=cfg.pixeldrain_api_key)

    with open(args.report, "r", encoding="utf-8") as f:
        sync_report = json.load(f)

    links = sync_report.get("links_found") or sync_report.get("links_new") or []
    if not isinstance(links, list):
        raise SystemExit("Report JSON missing links_found/links_new list")

    name_index: Optional[Dict[str, List[LocalFileHit]]] = None
    if args.global_search_by_name:
        name_index = _build_name_index(inv.traxdb_root)

    audit: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "traxdb_root": inv.traxdb_root,
        "report_source": os.path.abspath(args.report),
        "global_search_by_name": bool(args.global_search_by_name),
        "lists": [],
        "summary": {
            "lists_total": 0,
            "files_total": 0,
            "files_ok": 0,
            "files_missing": 0,
            "files_size_mismatch": 0,
        },
        "dead_links": [],
        "errors": [],
    }

    for l in links:
        try:
            list_id = str(l.get("list_id") or "").strip()
            inferred_date = l.get("inferred_date")
            if not list_id:
                continue

            dest_dir = _pick_dest_dir(inv.traxdb_root, inferred_date, list_id)
            files = list(client.iter_list_files(list_id))

            item = {
                "list_id": list_id,
                "pixeldrain_url": l.get("pixeldrain_url"),
                "inferred_date": inferred_date,
                "dest_dir": dest_dir,
                "files": [],
            }

            for pf in files:
                expected_path = os.path.join(dest_dir, os.path.basename(pf.name))
                status = "missing"
                actual_path = None
                actual_size = None

                if os.path.exists(expected_path):
                    actual_path = expected_path
                    actual_size = os.path.getsize(expected_path)
                    if pf.size is None or actual_size == pf.size:
                        status = "ok"
                    else:
                        status = "size_mismatch"
                elif name_index is not None and pf.name in name_index:
                    # best-effort: pick any hit; if size matches, prefer it
                    hits = name_index[pf.name]
                    chosen = hits[0]
                    if pf.size is not None:
                        for h in hits:
                            if h.size == pf.size:
                                chosen = h
                                break
                    actual_path = chosen.path
                    actual_size = chosen.size
                    if pf.size is None or actual_size == pf.size:
                        status = "ok_elsewhere"
                    else:
                        status = "size_mismatch_elsewhere"

                item["files"].append(
                    {
                        "name": pf.name,
                        "file_id": pf.id,
                        "expected_size": pf.size,
                        "expected_path": expected_path,
                        "status": status,
                        "actual_path": actual_path,
                        "actual_size": actual_size,
                    }
                )

            audit["lists"].append(item)
        except Exception as e:
            if _is_pixeldrain_not_found(e):
                audit["dead_links"].append({"list": l, "error": repr(e)})
            else:
                audit["errors"].append({"list": l, "error": repr(e)})

    # Summary
    audit["summary"]["lists_total"] = len(audit["lists"])
    for lst in audit["lists"]:
        for f in lst["files"]:
            audit["summary"]["files_total"] += 1
            st = f["status"]
            if st in ("ok", "ok_elsewhere"):
                audit["summary"]["files_ok"] += 1
            elif st in ("size_mismatch", "size_mismatch_elsewhere"):
                audit["summary"]["files_size_mismatch"] += 1
            else:
                audit["summary"]["files_missing"] += 1

    report_path = args.report_path or os.path.abspath(f"traxdb_audit_report_{_now_stamp()}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, ensure_ascii=False)

    print(f"Wrote audit report: {report_path}")
    print("Summary:", audit["summary"])
    if audit["dead_links"]:
        print(f"Dead links (Pixeldrain 404): {len(audit['dead_links'])}")
    if audit["errors"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


