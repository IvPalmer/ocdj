from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .config import load_config

from .slskd_adapter import SlskdAdapter, SlskdAdapterError, SlskdCandidate


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _normalize_text(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[\s_]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return s.strip()


def _tokenize(s: str) -> List[str]:
    # Lowercase, drop symbols, split on whitespace
    return [t for t in _normalize_text(s).split() if t]


def _ext_rank(filename: str) -> Optional[int]:
    fn = filename.lower()
    # handle .aif/.aiff
    if fn.endswith(".aiff") or fn.endswith(".aif"):
        return 0
    if fn.endswith(".flac"):
        return 1
    if fn.endswith(".wav"):
        return 2
    if fn.endswith(".mp3"):
        return 3
    return None


def _is_allowed_extra(filename: str) -> bool:
    fn = filename.lower()
    return fn.endswith((".cue", ".log", ".nfo", ".txt", ".jpg", ".jpeg", ".png", ".gif", ".pdf"))


def _split_parent_dir(path: str) -> str:
    # Soulseek paths can be unix-like or windows-like; preserve original separators
    p = path.rstrip("/\\")
    if "\\" in p and ("/" not in p or p.rfind("\\") > p.rfind("/")):
        return p.rsplit("\\", 1)[0] if "\\" in p else ""
    return p.rsplit("/", 1)[0] if "/" in p else ""


def _mp3_ok(c: SlskdCandidate) -> bool:
    if not c.filename.lower().endswith(".mp3"):
        return True
    # strict: only accept if we can confirm bitrate == 320
    return c.bitrate == 320


def _score_candidate(query: str, c: SlskdCandidate) -> Tuple[int, int, int]:
    """
    Lower is better.
    Tuple ordering:
    - format rank (AIFF < FLAC < WAV < MP3)
    - match distance (0 is perfect-ish)
    - size sort (prefer bigger for same match, so negative size as tie-breaker)
    """
    rank = _ext_rank(c.filename)
    if rank is None:
        rank = 999
    # Prefer non-locked results when possible.
    if getattr(c, "is_locked", False):
        rank += 50

    qn = _normalize_text(query)
    fn = _normalize_text(os.path.basename(c.filename))

    # crude distance: unmatched tokens count
    qtoks = set(qn.split())
    ftoks = set(fn.split())
    missing = len(qtoks - ftoks)

    size = c.size or 0
    return (rank, missing, -size)


def pick_best_candidate(query: str, cands: List[SlskdCandidate]) -> Optional[SlskdCandidate]:
    # Filter by allowed formats
    filtered: List[SlskdCandidate] = []
    for c in cands:
        # never try locked files
        if getattr(c, "is_locked", False):
            continue
        r = _ext_rank(c.filename)
        if r is None:
            continue
        if r == 3 and not _mp3_ok(c):
            continue
        filtered.append(c)
    if not filtered:
        return None
    filtered.sort(key=lambda c: _score_candidate(query, c))
    return filtered[0]


def _release_file_ok(c: SlskdCandidate) -> bool:
    if getattr(c, "is_locked", False):
        return False
    r = _ext_rank(c.filename)
    if r is None:
        return _is_allowed_extra(c.filename)
    if r == 3:
        return _mp3_ok(c)
    return True


def pick_best_release_group(query: str, cands: List[SlskdCandidate]) -> Optional[Dict[str, Any]]:
    """
    Group candidates by (username, parent_dir) and choose a group that looks like a full release folder.
    We then enqueue all qualifying files in that folder (tracks + allowed extras).
    """
    groups: Dict[Tuple[str, str], List[SlskdCandidate]] = {}
    for c in cands:
        if not c.username:
            continue
        if not _release_file_ok(c):
            continue
        parent = _split_parent_dir(c.filename)
        if not parent:
            continue
        groups.setdefault((c.username, parent), []).append(c)

    if not groups:
        return None

    def group_score(k: Tuple[str, str], files: List[SlskdCandidate]) -> Tuple[int, int, int, int, int]:
        # Prefer: best format present (min rank), lots of audio tracks, better match on folder name, and larger total size
        audio = [f for f in files if _ext_rank(f.filename) is not None]
        audio_count = len(audio)
        min_rank = min((_ext_rank(f.filename) or 999) for f in audio) if audio else 999
        locked_penalty = sum(1 for f in audio if getattr(f, "is_locked", False))
        folder = os.path.basename(k[1].replace("\\", "/"))
        missing = len(set(_normalize_text(query).split()) - set(_normalize_text(folder).split()))
        total_size = sum((f.size or 0) for f in audio)
        return (min_rank, locked_penalty, -audio_count, missing, -total_size)

    # Require at least 2 audio files to consider it a "release"
    viable = []
    for k, files in groups.items():
        audio = [f for f in files if _ext_rank(f.filename) is not None]
        if len(audio) >= 2:
            viable.append((k, files))
    if not viable:
        return None

    viable.sort(key=lambda kv: group_score(kv[0], kv[1]))
    (username, parent), files = viable[0]

    # Enqueue: all qualifying files in the folder; keep stable ordering
    files_sorted = sorted(files, key=lambda f: f.filename.lower())
    enqueue_files = [{"filename": f.filename, "size": f.size} for f in files_sorted if _release_file_ok(f) and isinstance(f.size, int)]
    return {
        "username": username,
        "folder": parent,
        "audio_files": len([f for f in files_sorted if _ext_rank(f.filename) is not None]),
        "total_files": len(files_sorted),
        "enqueue_files": enqueue_files,
    }


def read_wanted(path: str) -> List[str]:
    out: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(s)
    return out


def parse_wanted_line(line: str) -> Tuple[str, str]:
    """
    Supported formats:
    - track: "track: Artist - Title"
    - release: "release: Artist - Album" (downloads the best matching folder)
    If no prefix is provided, defaults to track.
    """
    s = line.strip()
    m = re.match(r"^(release)\s*:\s*(.+)$", s, flags=re.IGNORECASE)
    if m:
        return ("release", m.group(2).strip())
    # Accept both track: and legacy trac: (typo) for compatibility
    m = re.match(r"^(track|trac)\s*:\s*(.+)$", s, flags=re.IGNORECASE)
    if m:
        return ("track", m.group(2).strip())
    return ("track", s)


def write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _is_transfer_failure_state(state: str) -> bool:
    st = (state or "").lower()
    return ("rejected" in st) or ("errored" in st) or ("timedout" in st) or ("aborted" in st) or ("cancel" in st)


def _is_transfer_proven_ok(row: Dict[str, Any]) -> bool:
    """
    We need a stronger signal than "it exists in the queue" before committing to a source.
    OK:
    - InProgress
    - Completed, Succeeded
    NOT OK:
    - Queued/Initializing (not proven; often later becomes "Completed, Rejected: File not shared")
    - Completed, Rejected/Errored/TimedOut/etc
    """
    st = str(row.get("state") or row.get("status") or "")
    st_l = st.lower()
    if not st_l:
        return False
    if _is_transfer_failure_state(st_l):
        return False
    if "completed" in st_l:
        return "succeeded" in st_l
    if "inprogress" in st_l or "progress" in st_l:
        return True
    return False


def _is_transfer_acceptable_for_commit(row: Dict[str, Any], *, accept_queued: bool) -> bool:
    """
    Decide whether a probe transfer is "good enough" to commit to a source.
    - If accept_queued=True, then Queued/Initializing are allowed (common when remote queue is huge).
    - Failures are never acceptable.
    """
    st = str(row.get("state") or row.get("status") or "")
    st_l = st.lower()
    if _is_transfer_failure_state(st_l):
        return False
    if accept_queued and ("queued" in st_l or "initializ" in st_l):
        return True
    return _is_transfer_proven_ok(row)


def _should_preclean(state: str, mode: str) -> bool:
    """
    mode:
      - failed: remove only terminal failures (Completed, Rejected/Errored/TimedOut/Aborted/etc)
      - completed: remove all Completed transfers (including Succeeded)
      - none: do nothing
    """
    m = (mode or "none").lower()
    st = (state or "").lower()
    if m == "none" or not st:
        return False
    if "completed" not in st:
        return False
    if m == "completed":
        return True
    # failed
    return _is_transfer_failure_state(st)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="slskd-driven Soulseek search+download from wanted.txt")
    p.add_argument("--config", default=None, help="Config JSON path (defaults to soulseek_sync/config.json if present)")
    p.add_argument("--wanted", default="soulseek_sync/wanted.txt", help="Wanted list (one query per line)")
    p.add_argument(
        "--soulseek-root",
        default=None,
        help="Destination folder for downloads (should match slskd download dir). Defaults to config.soulseek_root or <repo>/soulseek",
    )
    p.add_argument("--dry-run", action="store_true", help="Search + select only; do not enqueue downloads.")
    p.add_argument(
        "--preclean",
        choices=["none", "failed", "completed"],
        default="failed",
        help="Before enqueuing (non-dry-run), remove existing Completed transfers from slskd. "
        "'failed' removes only Rejected/Errored/TimedOut/etc (default). 'completed' removes all Completed items.",
    )
    p.add_argument("--max-results", type=int, default=200, help="Max candidates to consider per query (best-effort).")
    p.add_argument("--max-attempts", type=int, default=3, help="How many alternative sources to try before giving up.")
    p.add_argument(
        "--probe-timeout-s",
        type=float,
        default=180.0,
        help="How long to wait for the initial 'probe' download to either start (InProgress) or finish. "
        "Longer helps with 'Queued, Remotely' sources.",
    )
    p.add_argument(
        "--accept-queued-probe",
        action="store_true",
        help="Treat probe states like 'Queued, Remotely/Locally' as acceptable and proceed to enqueue the rest. "
        "Useful for sources with large remote queue lengths.",
    )
    p.add_argument("--sleep-s", type=float, default=1.0, help="Sleep between queries to reduce load.")
    p.add_argument("--progress-path", default=None, help="Live progress JSON path (default: logs/soulseek_progress_<ts>.json)")
    p.add_argument("--report-path", default=None, help="Final report JSON path (default: logs/soulseek_report_<ts>.json)")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    slskd_base_url = cfg.slskd_base_url
    slskd_api_key = cfg.slskd_api_key

    # tools/soulseek_sync/run.py -> repo root is ../../
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    soulseek_root = args.soulseek_root or cfg.soulseek_root
    os.makedirs(soulseek_root, exist_ok=True)

    wanted = read_wanted(args.wanted)
    ts = _now_stamp()
    progress_path = args.progress_path or os.path.join(repo_root, "logs", f"soulseek_progress_{ts}.json")
    report_path = args.report_path or os.path.join(repo_root, "logs", f"soulseek_report_{ts}.json")

    out: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "wanted_path": os.path.abspath(args.wanted),
        "slskd_base_url": slskd_base_url,
        "soulseek_root": soulseek_root,
        "dry_run": bool(args.dry_run),
        "policy": {"order": ["aiff", "flac", "wav", "mp3@320"], "reject_lower": True},
        "summary": {
            "queries_total": len(wanted),
            "queries_processed": 0,
            "queries_enqueued": 0,
            "queries_no_match": 0,
            "errors": 0,
        },
        "items": [],
        "errors": [],
    }
    write_json(progress_path, out)

    try:
        adapter = SlskdAdapter(base_url=slskd_base_url, api_key=slskd_api_key)
    except SlskdAdapterError as e:
        raise SystemExit(str(e))

    # Optional pre-clean: keep the UI/queue tidy and prevent old failures from confusing status reports.
    if not args.dry_run and args.preclean != "none":
        try:
            rows = adapter.list_downloads_flat()
            removed = 0
            for r in rows:
                st = str(r.get("state") or r.get("status") or "")
                if not _should_preclean(st, args.preclean):
                    continue
                username = str(r.get("_username") or "")
                download_id = r.get("id")
                if not username or not download_id:
                    continue
                adapter.cancel_download(username=username, download_id=str(download_id), remove=True)
                removed += 1
            # Record in the report for debugging
            out["preclean"] = {"mode": args.preclean, "removed": removed}
            write_json(progress_path, out)
        except Exception as e:
            # Non-fatal: don't block downloads due to cleanup errors
            out.setdefault("preclean", {"mode": args.preclean})
            out["preclean"]["error"] = repr(e)
            write_json(progress_path, out)

    for q in wanted:
        mode, query = parse_wanted_line(q)
        item: Dict[str, Any] = {
            "query": query,
            "mode": mode,
            "search_variant_used": None,
            "chosen": None,
            "enqueued": False,
            "attempts": [],
            "reason": None,
        }
        try:
            all_cands: List[SlskdCandidate] = []
            cands = adapter.search(query)[: max(1, args.max_results)]
            item["search_variant_used"] = query
            if mode == "release":
                # Build ranked groups by repeatedly selecting best and removing its files
                remaining = list(cands)
                attempted = 0
                while attempted < max(1, int(args.max_attempts)):
                    grp = pick_best_release_group(query, remaining)
                    if not grp:
                        break
                    attempted += 1
                    item["attempts"].append({"type": "release", "candidate": grp, "result": None})
                    if args.dry_run:
                        item["chosen"] = grp
                        item["reason"] = "dry_run"
                        break

                    # Probe with the first audio file (fast fail if remote rejects)
                    probe = None
                    for f in grp["enqueue_files"]:
                        fn = f.get("filename")
                        if isinstance(fn, str) and _ext_rank(fn) is not None:
                            probe = f
                            break
                    if probe is None and grp["enqueue_files"]:
                        probe = grp["enqueue_files"][0]
                    if probe is None:
                        item["attempts"][-1]["result"] = {"status": "skipped", "reason": "no_enqueueable_files"}
                        # remove this group from remaining
                        remaining = [c for c in remaining if not (c.username == grp["username"] and _split_parent_dir(c.filename) == grp["folder"])]
                        continue

                    adapter.enqueue_downloads(username=grp["username"], files=[probe])
                    row = adapter.wait_for_download_terminal_or_progress(
                        username=grp["username"],
                        filename=probe["filename"],
                        timeout_s=float(args.probe_timeout_s),
                        require_progress=True,
                    )
                    st = str(row.get("state") or "")
                    item["attempts"][-1]["result"] = {
                        "probe": probe,
                        "state": st or None,
                        "exception": row.get("exception"),
                        "percentComplete": row.get("percentComplete"),
                        "bytesTransferred": row.get("bytesTransferred"),
                    }
                    if not _is_transfer_acceptable_for_commit(row, accept_queued=bool(args.accept_queued_probe)):
                        # cancel/remove if present
                        if row.get("id"):
                            adapter.cancel_download(username=grp["username"], download_id=str(row["id"]), remove=True)
                        # try next group
                        remaining = [
                            c
                            for c in remaining
                            if not (c.username == grp["username"] and _split_parent_dir(c.filename) == grp["folder"])
                        ]
                        continue

                    # Probe looks ok; enqueue rest (excluding the probe itself)
                    rest = [f for f in grp["enqueue_files"] if f.get("filename") != probe.get("filename")]
                    if rest:
                        adapter.enqueue_downloads(username=grp["username"], files=rest)
                    item["chosen"] = grp
                    item["enqueued"] = True
                    out["summary"]["queries_enqueued"] += 1
                    break

                if not item["enqueued"] and item.get("reason") is None:
                    out["summary"]["queries_no_match"] += 1
                    item["reason"] = "no_release_folder_matching_policy"
            else:
                # Try alternatives until one isn't immediately rejected/failed
                attempted = 0
                remaining = list(cands)
                while attempted < max(1, int(args.max_attempts)):
                    chosen = pick_best_candidate(query, remaining)
                    if not chosen:
                        break
                    attempted += 1
                    ch = {
                        "username": chosen.username,
                        "filename": chosen.filename,
                        "size": chosen.size,
                        "bitrate": chosen.bitrate,
                        "is_locked": getattr(chosen, "is_locked", False),
                    }
                    item["attempts"].append({"type": "track", "candidate": ch, "result": None})

                    if not chosen.username:
                        item["attempts"][-1]["result"] = {"status": "skipped", "reason": "missing_username"}
                        remaining = [c for c in remaining if c is not chosen]
                        continue
                    if not isinstance(chosen.size, int):
                        item["attempts"][-1]["result"] = {"status": "skipped", "reason": "missing_size"}
                        remaining = [c for c in remaining if c is not chosen]
                        continue
                    if args.dry_run:
                        item["chosen"] = ch
                        item["reason"] = "dry_run"
                        break

                    adapter.enqueue_downloads(username=chosen.username, files=[{"filename": chosen.filename, "size": chosen.size}])
                    row = adapter.wait_for_download_terminal_or_progress(
                        username=chosen.username,
                        filename=chosen.filename,
                        timeout_s=float(args.probe_timeout_s),
                        require_progress=True,
                    )
                    st = str(row.get("state") or "")
                    item["attempts"][-1]["result"] = {
                        "state": st or None,
                        "exception": row.get("exception"),
                        "percentComplete": row.get("percentComplete"),
                        "bytesTransferred": row.get("bytesTransferred"),
                    }
                    if not _is_transfer_acceptable_for_commit(row, accept_queued=bool(args.accept_queued_probe)):
                        if row.get("id"):
                            adapter.cancel_download(username=chosen.username, download_id=str(row["id"]), remove=True)
                        remaining = [c for c in remaining if c is not chosen]
                        continue

                    item["chosen"] = ch
                    item["enqueued"] = True
                    out["summary"]["queries_enqueued"] += 1
                    break

                if not item["enqueued"] and item.get("reason") is None:
                    out["summary"]["queries_no_match"] += 1
                    item["reason"] = "no_candidate_matching_policy"
        except Exception as e:
            out["summary"]["errors"] += 1
            out["errors"].append({"query": query, "mode": mode, "error": repr(e)})
            item["reason"] = "error"
        finally:
            out["items"].append(item)
            out["summary"]["queries_processed"] += 1
            write_json(progress_path, out)
            time.sleep(max(0.0, float(args.sleep_s)))

    write_json(report_path, out)
    return 0 if out["summary"]["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())


