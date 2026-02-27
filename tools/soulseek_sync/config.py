from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SoulseekConfig:
    slskd_base_url: str
    slskd_api_key: str
    soulseek_root: str


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def load_config(config_path: Optional[str] = None) -> SoulseekConfig:
    """
    Soulseek-only config loader.

    Priority:
    - explicit config_path JSON (if provided)
    - shared JSON: <repo>/djtools_config.json (if present)
    - env vars: SLSKD_BASE_URL, SLSKD_API_KEY, SOULSEEK_ROOT
    - default JSON: soulseek_sync/config.json (relative to repo root)
    """
    # tools/soulseek_sync/config.py -> repo root is ../../
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    tools_root = os.path.join(repo_root, "tools")
    default_json = os.path.join(tools_root, "soulseek_sync", "config.json")
    shared_json = os.path.join(repo_root, "djtools_config.json")

    data: Dict[str, Any] = {}
    if config_path:
        data = _read_json(config_path)
    else:
        if os.path.exists(default_json):
            data.update(_read_json(default_json))
        if os.path.exists(shared_json):
            # shared config is allowed to override defaults, but env overrides both
            data.update(_read_json(shared_json))

    # Env vars override JSON values (highest priority)
    slskd_base_url = (
        os.environ.get("SLSKD_BASE_URL", "").strip()
        or str(data.get("slskd_base_url") or "").strip()
    )
    slskd_api_key = (
        os.environ.get("SLSKD_API_KEY", "").strip()
        or str(data.get("slskd_api_key") or "").strip()
    )
    soulseek_root = (
        os.environ.get("SOULSEEK_ROOT", "").strip()
        or str(data.get("soulseek_root") or "").strip()
        or os.path.join(repo_root, "soulseek")
    )

    if not slskd_base_url or not slskd_api_key:
        raise SystemExit(
            "Missing Soulseek config. Create soulseek_sync/config.json (or pass --config) with "
            "'slskd_base_url' + 'slskd_api_key', or set env vars SLSKD_BASE_URL/SLSKD_API_KEY."
        )

    return SoulseekConfig(
        slskd_base_url=slskd_base_url,
        slskd_api_key=slskd_api_key,
        soulseek_root=soulseek_root,
    )


