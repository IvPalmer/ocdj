from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth


class PixeldrainError(RuntimeError):
    pass


@dataclass(frozen=True)
class PixeldrainFile:
    id: str
    name: str
    size: Optional[int] = None


class PixeldrainClient:
    """
    Pixeldrain API helper.

    Docs: https://pixeldrain.com/api
    Auth: HTTP Basic; API key is the password. Username can be empty.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        user_agent: str = "traxdb_sync/1.0",
        timeout_s: int = 60,
    ) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._timeout = timeout_s

        if api_key:
            self._session.auth = HTTPBasicAuth("", api_key)

    @staticmethod
    def parse_list_id(url_or_id: str) -> str:
        s = url_or_id.strip()
        # Accept either a raw list id or a URL like https://pixeldrain.com/l/<id>
        if "/l/" in s:
            s = s.split("/l/", 1)[1]
        s = s.split("?", 1)[0].split("#", 1)[0].strip("/")
        return s

    def get_list(self, list_id: str) -> Dict[str, Any]:
        url = f"https://pixeldrain.com/api/list/{list_id}"
        r = self._session.get(url, timeout=self._timeout)
        if r.status_code != 200:
            raise PixeldrainError(f"Pixeldrain list fetch failed ({r.status_code}): {r.text[:2000]}")
        return r.json()

    def iter_list_files(self, list_id: str) -> Iterable[PixeldrainFile]:
        data = self.get_list(list_id)
        files = data.get("files") or data.get("items") or []
        if not isinstance(files, list):
            raise PixeldrainError("Unexpected list response: 'files' is not a list")
        for f in files:
            if not isinstance(f, dict):
                continue
            file_id = str(f.get("id") or f.get("file_id") or "").strip()
            name = str(f.get("name") or f.get("filename") or "").strip()
            size = f.get("size")
            if isinstance(size, str) and size.isdigit():
                size = int(size)
            if not file_id or not name:
                continue
            yield PixeldrainFile(id=file_id, name=name, size=size if isinstance(size, int) else None)

    def download_file(
        self,
        file_id: str,
        dest_path: str,
        *,
        expected_size: Optional[int] = None,
        overwrite: bool = False,
        resume: bool = True,
        chunk_size: int = 1024 * 1024,
        max_retries: int = 5,
        retry_backoff_s: float = 1.5,
    ) -> Tuple[bool, int]:
        """
        Returns (downloaded, bytes_written).
        downloaded=False means it was skipped (already present and matched expected_size, when known).
        """
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

        if os.path.exists(dest_path) and not overwrite:
            if expected_size is not None and os.path.getsize(dest_path) == expected_size:
                return (False, 0)
            if not resume:
                return (False, 0)

        url = f"https://pixeldrain.com/api/file/{file_id}"

        attempt = 0
        while True:
            attempt += 1
            start = 0
            headers = {}
            mode = "wb"
            if resume and os.path.exists(dest_path):
                start = os.path.getsize(dest_path)
                if start > 0:
                    headers["Range"] = f"bytes={start}-"
                    mode = "ab"

            try:
                with self._session.get(url, headers=headers, stream=True, timeout=self._timeout) as r:
                    # 200 full body, 206 range
                    if r.status_code not in (200, 206):
                        raise PixeldrainError(f"Pixeldrain file download failed ({r.status_code}): {r.text[:2000]}")

                    # If we requested a range but server returned 200 (full body),
                    # reset to write mode to avoid appending full content after partial.
                    if mode == "ab" and r.status_code == 200:
                        mode = "wb"
                        start = 0

                    bytes_written = 0
                    with open(dest_path, mode) as f:
                        for chunk in r.iter_content(chunk_size=chunk_size):
                            if not chunk:
                                continue
                            f.write(chunk)
                            bytes_written += len(chunk)

                # Validate size if we know it
                if expected_size is not None:
                    actual = os.path.getsize(dest_path)
                    if actual != expected_size:
                        raise PixeldrainError(
                            f"Downloaded size mismatch for {dest_path}: expected={expected_size} actual={actual}"
                        )
                return (True, bytes_written)
            except Exception:
                if attempt >= max_retries:
                    raise
                time.sleep(min(60.0, retry_backoff_s ** attempt))

    def download_list(
        self,
        list_id: str,
        dest_dir: str,
        *,
        skip_existing: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Downloads every file in a Pixeldrain list into dest_dir.
        Returns per-file results suitable for reporting.
        """
        os.makedirs(dest_dir, exist_ok=True)
        results: List[Dict[str, Any]] = []
        for pf in self.iter_list_files(list_id):
            dest_path = os.path.join(dest_dir, os.path.basename(pf.name))
            downloaded, bytes_written = self.download_file(
                pf.id,
                dest_path,
                expected_size=pf.size,
                overwrite=not skip_existing,
                resume=True,
            )
            results.append(
                {
                    "file_id": pf.id,
                    "name": pf.name,
                    "expected_size": pf.size,
                    "dest_path": dest_path,
                    "downloaded": downloaded,
                    "bytes_written": bytes_written,
                }
            )
        return results


