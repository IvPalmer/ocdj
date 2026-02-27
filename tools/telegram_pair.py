#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

import requests


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_config() -> Dict[str, Any]:
    repo = _repo_root()
    cfg = _load_json(repo / "djtools_config.json")
    # env overrides
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram_bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    return cfg


def write_config_patch(*, chat_id: str) -> None:
    """
    Write telegram_chat_id into repo-root djtools_config.json (if it exists).
    """
    repo = _repo_root()
    path = repo / "djtools_config.json"
    if not path.exists():
        raise SystemExit(f"Missing config file: {path}")
    data = _load_json(path)
    data["telegram_chat_id"] = chat_id
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _api_get(token: str, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = requests.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not data.get("ok"):
        raise SystemExit(f"Telegram API error calling {method}: {data!r}")
    return data


def main() -> int:
    p = argparse.ArgumentParser(description="Find your Telegram chat_id by reading bot updates")
    p.add_argument("--watch", action="store_true", help="Long-poll until we see at least one message/update.")
    p.add_argument("--timeout-s", type=int, default=60, help="Long poll timeout (seconds) when --watch is used.")
    p.add_argument("--write", action="store_true", help="Write the first discovered chat_id into djtools_config.json")
    args = p.parse_args()

    cfg = load_config()
    token = str(cfg.get("telegram_bot_token") or "").strip()
    if not token or token == "REPLACE_ME":
        raise SystemExit(
            "Missing telegram_bot_token.\n"
            "- Put it in djtools_config.json under 'telegram_bot_token', OR\n"
            "- export TELEGRAM_BOT_TOKEN=...\n"
        )

    me = _api_get(token, "getMe").get("result") or {}
    bot_user = me.get("username") or "<unknown>"
    print(f"Bot OK: @{bot_user}")

    seen: Set[Tuple[str, str]] = set()
    offset = None

    def dump_updates(upds: list) -> Optional[str]:
        nonlocal seen
        found_chat_id: Optional[str] = None
        for u in upds:
            if not isinstance(u, dict):
                continue
            update_id = u.get("update_id")
            msg = u.get("message") or u.get("edited_message") or u.get("channel_post") or u.get("my_chat_member") or {}
            if not isinstance(msg, dict):
                continue
            chat = msg.get("chat") or {}
            if not isinstance(chat, dict):
                continue
            chat_id = chat.get("id")
            chat_type = chat.get("type")
            title = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
            if chat_id is None:
                continue
            key = (str(chat_id), str(chat_type or ""))
            if key in seen:
                continue
            seen.add(key)
            print(f"Found chat: id={chat_id} type={chat_type} title={title!r}")
            if found_chat_id is None:
                found_chat_id = str(chat_id)
        if upds:
            # advance offset to avoid re-printing
            last = upds[-1].get("update_id")
            if isinstance(last, int):
                return str(last + 1)
        return None

    if not args.watch:
        data = _api_get(token, "getUpdates", params={"limit": 100, "timeout": 0, "allowed_updates": ["message", "edited_message", "channel_post", "my_chat_member"]})
        updates = data.get("result") or []
        if not updates:
            print("No updates yet.")
            print("Send your bot a message in Telegram (e.g. /start), then rerun with --watch.")
            return 0
        new_offset = dump_updates(updates if isinstance(updates, list) else [])
        if args.write and seen:
            first_chat_id = next(iter(seen))[0]
            write_config_patch(chat_id=first_chat_id)
            print(f"Wrote telegram_chat_id={first_chat_id} to djtools_config.json")
        if new_offset:
            print(f"(next offset would be {new_offset})")
        return 0

    print("Watching for updates. Send the bot a message now (e.g. /start). Ctrl-C to stop.")
    try:
        while True:
            params = {
                "limit": 100,
                "timeout": int(args.timeout_s),
                "allowed_updates": ["message", "edited_message", "channel_post", "my_chat_member"],
            }
            if offset is not None:
                params["offset"] = offset
            data = _api_get(token, "getUpdates", params=params)
            updates = data.get("result") or []
            if isinstance(updates, list) and updates:
                offset = dump_updates(updates)
                if args.write and seen:
                    first_chat_id = next(iter(seen))[0]
                    write_config_patch(chat_id=first_chat_id)
                    print(f"Wrote telegram_chat_id={first_chat_id} to djtools_config.json")
                    return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


