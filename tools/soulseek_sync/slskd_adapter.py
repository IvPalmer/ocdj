from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote as _urlquote

import requests


class SlskdAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class SlskdCandidate:
    # Best-effort normalized fields across slskd/slskd-api versions
    username: Optional[str]
    filename: str
    size: Optional[int] = None
    bitrate: Optional[int] = None
    is_locked: bool = False
    raw: Optional[Dict[str, Any]] = None


def _coerce_int(v: Any) -> Optional[int]:
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        try:
            return int(v)
        except Exception:
            return None
    return None


def _pick_first(d: Dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def normalize_candidate(obj: Any) -> Optional[SlskdCandidate]:
    """
    Attempt to normalize a result object from slskd-api into a SlskdCandidate.
    We accept dict-like objects and ignore unknown shapes.
    """
    if obj is None:
        return None
    if not isinstance(obj, dict):
        # slskd-api may return model objects; try to convert best-effort
        if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
            try:
                obj = obj.dict()
            except Exception:
                return None
        else:
            return None

    filename = _pick_first(obj, ["filename", "file", "path", "fullFilename", "full_filename", "name"])
    if not isinstance(filename, str) or not filename.strip():
        return None
    filename = filename.strip()

    username = _pick_first(obj, ["username", "user", "owner", "fromUser", "from_user"])
    if isinstance(username, dict):
        username = _pick_first(username, ["username", "name"])
    if not isinstance(username, str):
        username = None

    size = _coerce_int(_pick_first(obj, ["size", "fileSize", "file_size", "length"]))
    bitrate = _coerce_int(_pick_first(obj, ["bitrate", "bitRate", "kbps"]))

    return SlskdCandidate(username=username, filename=filename, size=size, bitrate=bitrate, raw=obj)


class SlskdAdapter:
    """
    Thin client for the slskd REST API using X-API-Key auth.
    """

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": api_key})
        self._timeout_s = 30

    def search(self, query: str) -> List[SlskdCandidate]:
        """
        Return a list of candidates for the query.

        slskd search is async; we poll /responses briefly to allow results to arrive.
        We include both `files` and `lockedFiles` (flagging locked ones) because some
        slskd configurations may classify many results as locked.
        """
        q = query.strip()
        if not q:
            return []

        try:
            # POST /api/v0/searches
            url = f"{self._base_url}/api/v0/searches"
            payload = {
                "searchText": q,
                # be generous; we'll filter client-side
                "responseLimit": 200,
                "fileLimit": 10000,
                # don't filter responses server-side; we want full visibility
                "filterResponses": False,
                # slskd's API expects milliseconds here (despite some docs calling it seconds).
                # Using 15/30 causes immediate timeout and zero responses.
                "searchTimeout": 15000,
            }
            r = self._session.post(url, json=payload, timeout=self._timeout_s)
            if r.status_code == 409:
                raise SlskdAdapterError(
                    "slskd refused the search (HTTP 409). This usually means slskd is not connected to Soulseek yet."
                )
            if r.status_code != 200:
                raise SlskdAdapterError(f"slskd search failed ({r.status_code}): {r.text[:500]}")
            state = r.json()
            search_id = state.get("id")
            if not search_id:
                raise SlskdAdapterError(f"slskd search returned no id for query: {q!r}")

            # poll /responses
            resp_url = f"{self._base_url}/api/v0/searches/{search_id}/responses"
            responses: List[Dict[str, Any]] = []
            poll_deadline = time.time() + 25.0
            poll_interval_s = 0.8

            # Also watch the search state so we don't give up while responseCount > 0.
            state_url = f"{self._base_url}/api/v0/searches/{search_id}"

            while True:
                rr = self._session.get(resp_url, timeout=self._timeout_s)
                if rr.status_code == 200:
                    data = rr.json()
                    if isinstance(data, list) and len(data) > 0:
                        responses = data
                        break

                # Stop if we've timed out *and* the server reports the search is complete (or no responses).
                if time.time() >= poll_deadline:
                    try:
                        sr = self._session.get(state_url, timeout=self._timeout_s)
                        if sr.status_code == 200:
                            st = sr.json() if sr.headers.get("content-type", "").startswith("application/json") else {}
                            resp_count = st.get("responseCount")
                            is_complete = bool(st.get("isComplete"))
                            if is_complete or not resp_count:
                                break
                            # If there are responses but /responses hasn't materialized, give a little extra time.
                            poll_deadline = time.time() + 10.0
                        else:
                            break
                    except Exception:
                        break

                time.sleep(poll_interval_s)

            out: List[SlskdCandidate] = []
            for resp in responses:
                username = resp.get("username")
                files = resp.get("files") or []
                locked_files = resp.get("lockedFiles") or []
                if not isinstance(files, list):
                    files = []
                if not isinstance(locked_files, list):
                    locked_files = []

                def add_files(file_list: List[Dict[str, Any]], is_locked_default: bool) -> None:
                    for f in file_list:
                        if not isinstance(f, dict):
                            continue
                        filename = f.get("filename")
                        if not isinstance(filename, str) or not filename.strip():
                            continue
                        size = _coerce_int(f.get("size"))
                        bitrate = _coerce_int(f.get("bitRate") if "bitRate" in f else f.get("bitrate"))
                        is_locked = bool(f.get("isLocked")) if "isLocked" in f else is_locked_default
                        out.append(
                            SlskdCandidate(
                                username=username if isinstance(username, str) else None,
                                filename=filename.strip(),
                                size=size,
                                bitrate=bitrate,
                                is_locked=is_locked,
                                raw={"response": resp, "file": f},
                            )
                        )

                add_files(files, is_locked_default=False)
                add_files(locked_files, is_locked_default=True)

            return out
        except SlskdAdapterError:
            raise
        except Exception as e:
            raise SlskdAdapterError(f"slskd search failed for query {q!r}: {e!r}") from e

    def enqueue_downloads(self, *, username: str, files: List[Dict[str, Any]]) -> None:
        """
        Enqueue one or more downloads in slskd for a given user.
        Uses POST /api/v0/transfers/downloads/{username} with a list of {filename,size}.
        """
        if not files:
            return
        try:
            url = f"{self._base_url}/api/v0/transfers/downloads/{_urlquote(username, safe='')}"
            payload = []
            for f in files:
                fn = f.get("filename")
                sz = f.get("size")
                if not isinstance(fn, str) or not fn.strip():
                    continue
                if not isinstance(sz, int):
                    # slskd requires size; skip if unknown
                    continue
                payload.append({"filename": fn, "size": sz})
            if not payload:
                raise SlskdAdapterError("No enqueueable files (missing filename/size).")
            r = self._session.post(url, json=payload, timeout=self._timeout_s)
            if r.status_code not in (200, 201):
                raise SlskdAdapterError(f"slskd enqueue failed ({r.status_code}): {r.text[:500]}")
        except SlskdAdapterError:
            raise
        except Exception as e:
            raise SlskdAdapterError(f"slskd enqueue failed for {username=} files={len(files)}: {e!r}") from e

    def list_downloads_flat(self) -> List[Dict[str, Any]]:
        """
        Returns a flattened list of downloads with keys:
        - username, directory, id, filename, state, size, bytesTransferred (if present)
        """
        url = f"{self._base_url}/api/v0/transfers/downloads"
        r = self._session.get(url, timeout=self._timeout_s)
        if r.status_code != 200:
            raise SlskdAdapterError(f"slskd downloads list failed ({r.status_code}): {r.text[:500]}")
        data = r.json()
        out: List[Dict[str, Any]] = []
        if not isinstance(data, list):
            return out
        for u in data:
            username = u.get("username")
            for d in (u.get("directories") or []):
                directory = d.get("directory")
                for f in (d.get("files") or []):
                    if not isinstance(f, dict):
                        continue
                    row = dict(f)
                    row["_username"] = username
                    row["_directory"] = directory
                    out.append(row)
        return out

    def wait_for_download_terminal_or_progress(
        self,
        *,
        username: str,
        filename: str,
        timeout_s: float = 20.0,
        poll_s: float = 1.0,
        require_progress: bool = False,
    ) -> Dict[str, Any]:
        """
        Poll the downloads list until we find the matching transfer and it is either:
        - terminal (Completed, Rejected/Errored/Succeeded/TimedOut/etc), or
        - clearly started (InProgress), or
        - (if require_progress=False) queued/initializing is also accepted
        - (if require_progress=True) we keep polling until InProgress, percentComplete>0, or Completed
        Returns the best matching row (may be empty if not found).
        """
        deadline = time.time() + max(1.0, timeout_s)
        best: Dict[str, Any] = {}
        while time.time() < deadline:
            rows = self.list_downloads_flat()
            # filter by user + filename exact match
            matches = [
                r
                for r in rows
                if str(r.get("_username") or "") == username
                and str(r.get("filename") or r.get("fileName") or "") == filename
            ]
            if matches:
                # take the last one (newest-ish)
                best = matches[-1]
                st = str(best.get("state") or best.get("status") or "")
                st_l = st.lower()
                if "completed" in st_l:
                    return best
                if "progress" in st_l or "inprogress" in st_l:
                    return best
                pc = best.get("percentComplete")
                if require_progress and isinstance(pc, (int, float)) and pc > 0:
                    return best
                if not require_progress and ("queued" in st_l or "initializing" in st_l):
                    return best
            time.sleep(poll_s)
        return best

    def cancel_download(self, *, username: str, download_id: str, remove: bool = True) -> None:
        url = f"{self._base_url}/api/v0/transfers/downloads/{_urlquote(username, safe='')}/{_urlquote(download_id, safe='')}"
        r = self._session.delete(url, params={"remove": "true" if remove else "false"}, timeout=self._timeout_s)
        if r.status_code in (200, 204, 404):
            return

        # slskd rejects "remove=true" for downloads that aren't complete yet.
        # We still want to cancel them to keep retries moving, so fall back to remove=false.
        if remove and r.status_code == 500 and "before it is complete" in (r.text or "").lower():
            r2 = self._session.delete(url, params={"remove": "false"}, timeout=self._timeout_s)
            if r2.status_code in (200, 204, 404):
                return
            raise SlskdAdapterError(f"slskd cancel failed ({r2.status_code}): {r2.text[:500]}")

        raise SlskdAdapterError(f"slskd cancel failed ({r.status_code}): {r.text[:500]}")


