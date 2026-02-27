from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SyncConfig:
    pixeldrain_api_key: Optional[str] = None
    traxdb_cookies: Optional[str] = None
    traxdb_start_url: Optional[str] = None
    slskd_base_url: Optional[str] = None
    slskd_api_key: Optional[str] = None
    soulseek_root: Optional[str] = None


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        import sys
        print(f"WARNING: failed to parse config {path}: {e}", file=sys.stderr)
        return {}


def load_config(explicit_path: Optional[str] = None) -> SyncConfig:
    """
    Config precedence (last wins):
    - ~/.config/dj-tools/config.json
    - ~/.config/traxdb_sync/config.json
    - <repo>/traxdb_sync/config.json
    - <repo>/djtools_config.json
    - explicit_path (if provided) overrides all file-based defaults
    - env vars: PIXELDRAIN_API_KEY, TRAXDB_COOKIES, TRAXDB_START_URL, SLSKD_BASE_URL, SLSKD_API_KEY, SOULSEEK_ROOT (highest)
    """
    merged: Dict[str, Any] = {}

    # default locations (lowest priority first)
    merged.update(_load_json(Path.home() / ".config" / "dj-tools" / "config.json"))
    merged.update(_load_json(Path.home() / ".config" / "traxdb_sync" / "config.json"))
    merged.update(_load_json(Path(__file__).resolve().parent / "config.json"))
    # optional shared config at repo root
    merged.update(_load_json(Path(__file__).resolve().parents[2] / "djtools_config.json"))

    # explicit path has highest file-based priority (overrides all defaults)
    if explicit_path:
        merged.update(_load_json(Path(explicit_path).expanduser()))

    # env overrides
    merged["pixeldrain_api_key"] = os.environ.get("PIXELDRAIN_API_KEY") or merged.get("pixeldrain_api_key")
    merged["traxdb_cookies"] = os.environ.get("TRAXDB_COOKIES") or merged.get("traxdb_cookies")
    merged["traxdb_start_url"] = os.environ.get("TRAXDB_START_URL") or merged.get("traxdb_start_url")
    merged["slskd_base_url"] = os.environ.get("SLSKD_BASE_URL") or merged.get("slskd_base_url")
    merged["slskd_api_key"] = os.environ.get("SLSKD_API_KEY") or merged.get("slskd_api_key")
    merged["soulseek_root"] = os.environ.get("SOULSEEK_ROOT") or merged.get("soulseek_root")

    return SyncConfig(
        pixeldrain_api_key=merged.get("pixeldrain_api_key") or None,
        traxdb_cookies=merged.get("traxdb_cookies") or None,
        traxdb_start_url=merged.get("traxdb_start_url") or None,
        slskd_base_url=merged.get("slskd_base_url") or None,
        slskd_api_key=merged.get("slskd_api_key") or None,
        soulseek_root=merged.get("soulseek_root") or None,
    )


