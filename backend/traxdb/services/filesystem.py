"""Filesystem rules for deciding which TraxDB date folders are complete."""

from __future__ import annotations

import os
from typing import Iterable


MEDIA_EXTENSIONS = {'.flac', '.wav', '.aiff', '.aif', '.mp3'}


def directory_has_media_files(path: str) -> bool:
    """Return whether a directory contains at least one regular media file."""
    try:
        with os.scandir(path) as entries:
            return any(
                entry.is_file() and os.path.splitext(entry.name)[1].lower() in MEDIA_EXTENSIONS
                for entry in entries
            )
    except OSError:
        return False


def latest_media_date(root: str, date_dir_names: Iterable[str]) -> str | None:
    """Return the newest date folder that actually contains media."""
    populated = [
        name for name in date_dir_names
        if directory_has_media_files(os.path.join(root, name))
    ]
    return max(populated) if populated else None
