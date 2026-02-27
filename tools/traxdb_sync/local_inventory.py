from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set


DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class LocalInventory:
    traxdb_root: str
    date_dirs: List[str]  # YYYY-MM-DD
    max_date_dir: Optional[str]  # YYYY-MM-DD
    flac_basenames: Set[str]
    list_ids_seen: Set[str]


def scan_local_traxdb(traxdb_root: str) -> LocalInventory:
    root = Path(traxdb_root)
    if not root.exists():
        raise FileNotFoundError(traxdb_root)

    date_dirs: List[str] = []
    flac_basenames: Set[str] = set()

    for p in root.iterdir():
        if p.is_dir() and DATE_DIR_RE.match(p.name):
            date_dirs.append(p.name)
            for ext in ("*.flac", "*.wav", "*.aiff", "*.aif", "*.mp3"):
                for f in p.glob(ext):
                    flac_basenames.add(f.name)

    date_dirs.sort()
    max_date_dir: Optional[str] = date_dirs[-1] if date_dirs else None

    # If we keep a local "seen list id" file, include it
    list_ids_seen: Set[str] = set()
    seen_path = root / ".pixeldrain_lists_seen.json"
    if seen_path.exists():
        try:
            data = json.loads(seen_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                list_ids_seen = {str(x) for x in data}
        except Exception:
            pass

    return LocalInventory(
        traxdb_root=str(root),
        date_dirs=date_dirs,
        max_date_dir=max_date_dir,
        flac_basenames=flac_basenames,
        list_ids_seen=list_ids_seen,
    )


def mark_list_ids_seen(traxdb_root: str, list_ids: Iterable[str]) -> None:
    root = Path(traxdb_root)
    seen_path = root / ".pixeldrain_lists_seen.json"
    existing: Set[str] = set()
    if seen_path.exists():
        try:
            data = json.loads(seen_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                existing = {str(x) for x in data}
        except Exception:
            existing = set()

    merged = sorted(existing | {str(x) for x in list_ids})
    tmp_path = seen_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(seen_path)


