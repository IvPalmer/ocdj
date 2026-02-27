from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    from .config import load_config
    from .pixeldrain import PixeldrainClient
except ImportError:
    from config import load_config  # type: ignore[no-redef]
    from pixeldrain import PixeldrainClient  # type: ignore[no-redef]


@dataclass(frozen=True)
class ExpectedFile:
    expected_size: Optional[int]
    list_id: str


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _load_report(report_path: str, links_key: str) -> List[dict]:
    with open(report_path, "r", encoding="utf-8") as f:
        r = json.load(f)
    links = r.get(links_key) or []
    if not isinstance(links, list):
        raise SystemExit(f"Report key {links_key} is not a list")
    return links


def _build_expected_map(
    client: PixeldrainClient, links: List[dict], *, only_date: Optional[str]
) -> Dict[str, ExpectedFile]:
    """
    Basename -> expected (size,list_id). If the same basename appears with different sizes across lists,
    we keep the first and record the conflict in stdout.
    """
    out: Dict[str, ExpectedFile] = {}
    conflicts: List[Tuple[str, ExpectedFile, ExpectedFile]] = []
    for l in links:
        if only_date and l.get("inferred_date") != only_date:
            continue
        list_id = str(l.get("list_id") or "").strip()
        if not list_id:
            continue
        for pf in client.iter_list_files(list_id):
            existing = out.get(pf.name)
            cur = ExpectedFile(expected_size=pf.size, list_id=list_id)
            if existing is None:
                out[pf.name] = cur
            else:
                if existing.expected_size != cur.expected_size:
                    conflicts.append((pf.name, existing, cur))
    if conflicts:
        print(f"WARNING: {len(conflicts)} filename size conflicts across lists. Using first-seen mapping.")
        for name, a, b in conflicts[:20]:
            print(f"  CONFLICT: {name} size {a.expected_size} (list {a.list_id}) vs {b.expected_size} (list {b.list_id})")
        if len(conflicts) > 20:
            print("  ... more conflicts omitted ...")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Clean up '*.pixeldrain' sidecar files created on size mismatches")
    p.add_argument("--config", default=None, help="Optional config JSON path")
    p.add_argument("--report", required=True, help="Sync report JSON (contains list IDs)")
    p.add_argument("--links-key", default="links_found", help="links_found or links_new (default links_found)")
    p.add_argument("--traxdb-root", required=True, help="Path to local traxdb root")
    p.add_argument(
        "--only-date",
        default=None,
        help="Limit cleanup to a specific date folder (YYYY-MM-DD). If omitted, scans all date folders under traxdb root.",
    )
    p.add_argument(
        "--backup-dir",
        default=None,
        help="Backup directory for files we overwrite/delete (default: <traxdb_root>/_cleanup_backups/<timestamp>/)",
    )
    p.add_argument("--apply", action="store_true", help="Actually perform changes. Without this, runs a dry-run.")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    if not cfg.pixeldrain_api_key:
        raise SystemExit("Missing Pixeldrain API key (PIXELDRAIN_API_KEY or config.json)")

    traxdb_root = args.traxdb_root
    if not os.path.isdir(traxdb_root):
        raise SystemExit(f"traxdb root not found: {traxdb_root}")

    backup_root = args.backup_dir or os.path.join(traxdb_root, "_cleanup_backups", _now_stamp())
    if args.apply:
        os.makedirs(backup_root, exist_ok=True)

    client = PixeldrainClient(api_key=cfg.pixeldrain_api_key)
    links = _load_report(args.report, args.links_key)

    expected = _build_expected_map(client, links, only_date=args.only_date)

    # Find .pixeldrain files
    targets: List[str] = []
    if args.only_date:
        targets.append(os.path.join(traxdb_root, args.only_date))
    else:
        # scan only date-like folders plus _inbox
        for name in os.listdir(traxdb_root):
            pth = os.path.join(traxdb_root, name)
            if os.path.isdir(pth):
                targets.append(pth)

    planned = {"replace": 0, "delete_sidecar": 0, "rename": 0, "keep": 0, "unknown": 0}

    for folder in targets:
        if not os.path.isdir(folder):
            continue
        folder_label = os.path.basename(folder)
        for name in os.listdir(folder):
            if not name.endswith(".pixeldrain"):
                continue
            sidecar_path = os.path.join(folder, name)
            base_name = name[: -len(".pixeldrain")]
            base_path = os.path.join(folder, base_name)

            try:
                sidecar_size = os.path.getsize(sidecar_path)
            except OSError:
                continue

            exp = expected.get(base_name)
            exp_size = exp.expected_size if exp else None

            if not os.path.exists(base_path):
                # rename sidecar into place
                planned["rename"] += 1
                print(f"RENAME  {sidecar_path}  ->  {base_path}")
                if args.apply:
                    shutil.move(sidecar_path, base_path)
                continue

            try:
                base_size = os.path.getsize(base_path)
            except OSError:
                planned["unknown"] += 1
                print(f"UNKNOWN {sidecar_path} (can't stat base)")
                continue

            # If base and sidecar have the same size, the sidecar is redundant (almost certainly identical bytes).
            # Prefer keeping the base filename and deleting the sidecar.
            if base_size == sidecar_size:
                planned["delete_sidecar"] += 1
                print(f"DELETE  {sidecar_path}  (redundant: same size as base)")
                if args.apply:
                    b = os.path.join(backup_root, f"{folder_label}__{os.path.basename(sidecar_path)}")
                    shutil.copy2(sidecar_path, b)
                    os.remove(sidecar_path)
                continue

            # Determine which file matches expected size (if known)
            if exp_size is not None:
                if base_size == exp_size and sidecar_size != exp_size:
                    planned["delete_sidecar"] += 1
                    print(f"DELETE  {sidecar_path}  (base matches expected)")
                    if args.apply:
                        # backup then delete
                        b = os.path.join(backup_root, f"{folder_label}__{os.path.basename(sidecar_path)}")
                        shutil.copy2(sidecar_path, b)
                        os.remove(sidecar_path)
                    continue
                if sidecar_size == exp_size and base_size != exp_size:
                    planned["replace"] += 1
                    print(f"REPLACE {base_path}  (size {base_size})  <-  {sidecar_path} (matches expected {exp_size})")
                    if args.apply:
                        # backup base then replace
                        b = os.path.join(backup_root, f"{folder_label}__{os.path.basename(base_path)}")
                        shutil.copy2(base_path, b)
                        os.replace(sidecar_path, base_path)
                    continue

            # No expected size known, or neither matches expected: keep both
            planned["keep"] += 1
            print(f"KEEP    {base_path} (size {base_size}) and {sidecar_path} (size {sidecar_size})")

    print("Planned:", planned)
    if not args.apply:
        print("Dry-run only. Re-run with --apply to perform changes.")
    else:
        print(f"Backups (if any) saved to: {backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


