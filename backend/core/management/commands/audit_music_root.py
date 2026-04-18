"""Audit + clean the legacy music-root directory.

Dry-run by default. Reports everything; applies nothing. Pass --apply to execute.

Concerns it handles:
  1. Archive legacy, obviously-cruft folders → _archive_<date>/
  2. Delete trash (.DS_Store, stale .venv/)
  3. Sweep loose audio files at the pipeline root into soulseek/01_downloaded/_to_triage/
     (so auto_ingest can rediscover them on its next pass)
  4. Remove ghost 04_ready/ if empty (only 05_ready is canonical)
  5. Report unknown top-level folders that look like user content — but do NOT move them
     without an explicit --reclassify <folder> flag (safety).

The command never touches traxdb/, the configured soulseek/ pipeline stage dirs,
05_ready/, or soulseek_sync/ (slskd state).
"""
from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path
from typing import Iterable

from django.core.management.base import BaseCommand

from core.services.config import get_config

AUDIO_EXTS = {'.mp3', '.flac', '.aiff', '.aif', '.wav', '.m4a', '.ogg'}

# Folders at the pipeline root that are known cruft from previous eras of the
# project. Safe to archive wholesale.
KNOWN_CRUFT_DIRS = {
    'complete', 'flacs', 'sets', 'downloading', 'conversion',
    'logs', '04_ready',
}

# Folders at the pipeline root that should never be touched by the audit.
# Config-driven paths are resolved dynamically; these are just the extra names
# that don't live in config (slskd state dir) or are known safe (traxdb).
NEVER_TOUCH_DIRS = {'soulseek_sync', 'traxdb'}

# Canonical pipeline stage folders we keep inside the soulseek root.
STAGE_DIRS = {'01_downloaded', '02_tagged', '03_renamed', '04_converted', '05_ready'}

# Always-trash basenames.
TRASH = {'.DS_Store'}


class Plan:
    """Collects planned actions before executing."""

    def __init__(self):
        self.archive: list[tuple[Path, str]] = []   # (src, reason)
        self.delete: list[tuple[Path, str]] = []
        self.sweep: list[tuple[Path, Path, str]] = []  # (src, dest, reason)
        self.report_only: list[tuple[Path, str]] = []

    def empty(self) -> bool:
        return not (self.archive or self.delete or self.sweep)


class Command(BaseCommand):
    help = 'Audit and clean the legacy ID3 music-root directory (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually perform the moves/deletes. Default is dry-run.',
        )
        parser.add_argument(
            '--reclassify', action='append', default=[],
            help='Name of a user-content folder to also sweep into triage '
                 '(can repeat). Example: --reclassify "rafa" --reclassify "to fix"',
        )
        parser.add_argument(
            '--archive-dir', default='',
            help='Archive destination (default: <library-root>/_archive_<date>/).',
        )

    def handle(self, *args, **opts):
        apply_changes = opts['apply']
        reclassify = set(opts['reclassify'])

        # Resolve paths from config (H1 plumbing).
        music_root = Path(get_config('MUSIC_ROOT'))
        pipeline_root = Path(get_config('SOULSEEK_DOWNLOAD_ROOT'))
        traxdb_root = Path(get_config('TRAXDB_ROOT'))
        library_root = Path(get_config('ELECTRONIC_LIBRARY_ROOT'))

        # Host-side equivalents: inside the backend container the paths are
        # /music/... but on the user's Mac we're cleaning the real filesystem
        # directly via `docker exec`. Use the pipeline root *parent* (the
        # legacy "ID3" directory) as the audit target.
        audit_target = pipeline_root.parent
        archive_dir = Path(opts['archive_dir']) if opts['archive_dir'] \
            else library_root / f'_archive_{date.today().isoformat()}'
        triage_dir = pipeline_root / '01_downloaded' / '_to_triage'

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\nOCDJ music-root audit — {"APPLY" if apply_changes else "DRY RUN"}\n'))
        self.stdout.write(f'  audit target:  {audit_target}')
        self.stdout.write(f'  pipeline root: {pipeline_root}')
        self.stdout.write(f'  traxdb root:   {traxdb_root}')
        self.stdout.write(f'  archive dir:   {archive_dir}')
        self.stdout.write(f'  triage dir:    {triage_dir}\n')

        if not audit_target.exists():
            self.stdout.write(self.style.ERROR(f'Audit target does not exist: {audit_target}'))
            return

        plan = Plan()
        self._plan_cruft_and_trash(audit_target, plan)
        self._plan_loose_audio_sweep(audit_target, triage_dir, plan)
        self._plan_ghost_stage_dirs(pipeline_root, plan)
        self._plan_reclassify(audit_target, reclassify, triage_dir, plan)
        self._plan_stray_pipeline_subdirs(pipeline_root, triage_dir, plan)
        self._report_unknown_dirs(audit_target, plan, reclassify)

        self._print_plan(plan, archive_dir)

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                '\n(dry-run) pass --apply to execute.'))
            return

        if plan.empty():
            self.stdout.write(self.style.SUCCESS('\nNothing to do.'))
            return

        self._execute(plan, archive_dir, triage_dir)
        self.stdout.write(self.style.SUCCESS('\nDone.'))

    # ── planning ──────────────────────────────────────────────

    def _plan_cruft_and_trash(self, root: Path, plan: Plan):
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if entry.name in TRASH:
                plan.delete.append((entry, 'trash file'))
                continue
            if entry.name == '.venv':
                plan.delete.append((entry, 'stale python venv'))
                continue
            if entry.is_dir() and entry.name in KNOWN_CRUFT_DIRS:
                plan.archive.append((entry, 'legacy folder'))

    def _plan_loose_audio_sweep(self, root: Path, triage: Path, plan: Plan):
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if entry.is_file() and entry.suffix.lower() in AUDIO_EXTS:
                plan.sweep.append((entry, triage, 'loose audio at pipeline-root parent'))

    def _plan_ghost_stage_dirs(self, pipeline_root: Path, plan: Plan):
        # Historical 04_ready/ existed before conversion was added. 05_ready is canonical.
        ghost = pipeline_root / '04_ready'
        if ghost.exists() and ghost.is_dir():
            try:
                has_content = any(ghost.iterdir())
            except OSError:
                has_content = False
            if not has_content:
                plan.delete.append((ghost, 'empty ghost stage dir'))

    def _plan_reclassify(self, root: Path, names: Iterable[str], triage: Path, plan: Plan):
        for name in names:
            target = root / name
            if not target.exists():
                self.stdout.write(self.style.WARNING(
                    f'  [reclassify skipped — not found]: {target}'))
                continue
            for path in target.rglob('*'):
                if path.is_file() and path.suffix.lower() in AUDIO_EXTS:
                    plan.sweep.append((path, triage, f'reclassify {name}/'))
            plan.archive.append((target, f'reclassify {name}/ (shell)'))

    def _plan_stray_pipeline_subdirs(self, pipeline_root: Path, triage: Path, plan: Plan):
        """A raw slskd download sometimes lands as `pipeline_root/<release name>/...`
        instead of moving through 01_downloaded. Sweep those into triage so they
        enter the pipeline on next run."""
        if not pipeline_root.exists():
            return
        for entry in sorted(pipeline_root.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            if entry.name in STAGE_DIRS:
                continue
            # Stray release dir. Sweep any audio inside into triage.
            any_audio = False
            for path in entry.rglob('*'):
                if path.is_file() and path.suffix.lower() in AUDIO_EXTS:
                    plan.sweep.append((path, triage, f'stray pipeline dir {entry.name}/'))
                    any_audio = True
            # Whether it had audio or not, the stray shell should go.
            reason = 'empty after stray sweep' if any_audio else 'empty stray pipeline dir'
            plan.archive.append((entry, f'{reason}: {entry.name}/'))

    def _report_unknown_dirs(self, root: Path, plan: Plan, reclassify: set):
        """Any dir not recognized by the rules above gets reported (no action)."""
        handled_names = KNOWN_CRUFT_DIRS | NEVER_TOUCH_DIRS | reclassify | {'soulseek'}
        already_actioned = {p.name for p, _ in plan.archive}
        already_actioned |= {p.name for p, _ in plan.delete}
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            if entry.name in handled_names or entry.name.startswith('_archive_'):
                continue
            if entry.name in already_actioned:
                continue
            plan.report_only.append((entry, 'unclassified — use --reclassify to sweep'))

    # ── output ────────────────────────────────────────────────

    def _print_plan(self, plan: Plan, archive_dir: Path):
        def section(title, items, fmt):
            if not items:
                return
            self.stdout.write('\n' + self.style.MIGRATE_LABEL(title))
            for row in items:
                self.stdout.write('  ' + fmt(row))

        section(
            f'Archive → {archive_dir}',
            plan.archive,
            lambda r: f'{r[0]}  [{r[1]}]',
        )
        section(
            'Delete',
            plan.delete,
            lambda r: f'{r[0]}  [{r[1]}]',
        )
        section(
            'Sweep to triage',
            plan.sweep,
            lambda r: f'{r[0]}  →  {r[1]}  [{r[2]}]',
        )
        section(
            'Unclassified (no action)',
            plan.report_only,
            lambda r: f'{r[0]}  [{r[1]}]',
        )

        total = len(plan.archive) + len(plan.delete) + len(plan.sweep)
        self.stdout.write(self.style.SUCCESS(
            f'\n{total} actionable items '
            f'({len(plan.archive)} archive, {len(plan.delete)} delete, {len(plan.sweep)} sweep) '
            f'+ {len(plan.report_only)} unclassified.'))

    # ── execution ─────────────────────────────────────────────

    def _execute(self, plan: Plan, archive_dir: Path, triage_dir: Path):
        archive_dir.mkdir(parents=True, exist_ok=True)
        triage_dir.mkdir(parents=True, exist_ok=True)

        for src, dest_dir, reason in plan.sweep:
            self._safe_move(src, dest_dir / src.name)

        # Archive after sweeping so now-emptied folders still exist to be archived.
        for src, reason in plan.archive:
            if not src.exists():
                continue
            self._safe_move(src, archive_dir / src.name)

        for src, reason in plan.delete:
            try:
                if src.is_dir():
                    shutil.rmtree(src)
                else:
                    src.unlink()
                self.stdout.write(f'  deleted {src}')
            except OSError as e:
                self.stdout.write(self.style.ERROR(f'  failed to delete {src}: {e}'))

    def _safe_move(self, src: Path, dest: Path):
        """Move with collision avoidance."""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            final = dest
            i = 1
            while final.exists():
                stem = dest.stem
                suffix = dest.suffix
                final = dest.with_name(f'{stem}__{i}{suffix}')
                i += 1
            shutil.move(str(src), str(final))
            self.stdout.write(f'  moved {src}  →  {final}')
        except OSError as e:
            self.stdout.write(self.style.ERROR(f'  failed to move {src}: {e}'))
