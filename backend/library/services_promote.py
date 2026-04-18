"""Promote a ready LibraryTrack into the review staging folder.

Copies the file from 05_ready/ into REVIEW_FOLDER. The user reviews there
and drags approved tracks into the DJ library + iTunes manually — the tool
does not touch Music.app or the library proper.

Files are COPIED, not moved. 05_ready stays intact so promotion is
repeatable and a future classifier can still fingerprint the ready copy.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass

from core.services.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class PromoteResult:
    ok: bool
    source_path: str
    review_path: str = ''
    skipped: list[str] = None
    errors: list[str] = None

    def as_dict(self):
        return {
            'ok': self.ok,
            'source_path': self.source_path,
            'review_path': self.review_path,
            'skipped': self.skipped or [],
            'errors': self.errors or [],
        }


def promote_track(track) -> PromoteResult:
    """Copy a LibraryTrack file into the review folder."""
    src = track.file_path
    if not src or not os.path.exists(src):
        return PromoteResult(
            ok=False, source_path=src or '',
            errors=[f'source file missing: {src}'],
        )

    review = get_config('REVIEW_FOLDER')
    if not review:
        return PromoteResult(
            ok=False, source_path=src,
            errors=['REVIEW_FOLDER not configured'],
        )

    try:
        os.makedirs(review, exist_ok=True)
    except OSError as e:
        return PromoteResult(
            ok=False, source_path=src,
            errors=[f'cannot create {review}: {e}'],
        )

    dest = os.path.join(review, os.path.basename(src))
    if os.path.exists(dest):
        return PromoteResult(
            ok=True, source_path=src, review_path=dest,
            skipped=['review: already_exists'],
        )

    try:
        shutil.copy2(src, dest)
        logger.info(f'promoted {src} → {dest}')
        return PromoteResult(ok=True, source_path=src, review_path=dest)
    except OSError as e:
        return PromoteResult(
            ok=False, source_path=src,
            errors=[f'copy failed: {e}'],
        )
