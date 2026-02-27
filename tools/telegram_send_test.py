#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict

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
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram_bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg["telegram_chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
    return cfg


def main() -> int:
    p = argparse.ArgumentParser(description="Send a Telegram test message using the bot token + chat_id")
    p.add_argument("--message", default="dj-tools test message", help="Text to send")
    args = p.parse_args()

    cfg = load_config()
    token = str(cfg.get("telegram_bot_token") or "").strip()
    chat_id = str(cfg.get("telegram_chat_id") or "").strip()
    if not token or token == "REPLACE_ME":
        raise SystemExit("Missing telegram_bot_token in djtools_config.json (or TELEGRAM_BOT_TOKEN env var).")
    if not chat_id or chat_id == "REPLACE_ME":
        raise SystemExit("Missing telegram_chat_id in djtools_config.json (or TELEGRAM_CHAT_ID env var).")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": args.message, "disable_web_page_preview": True}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not data.get("ok"):
        raise SystemExit(f"Telegram sendMessage failed: {data!r}")
    print("OK: sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


