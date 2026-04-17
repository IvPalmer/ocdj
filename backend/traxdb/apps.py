import os
import sys
import logging

from django.apps import AppConfig
from django.db.utils import OperationalError, ProgrammingError


logger = logging.getLogger(__name__)


class TraxdbConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'traxdb'

    def ready(self):
        # Long-running sync/download/audit ops live in worker threads inside
        # the Django process. A container restart kills those threads but the
        # DB row stays in 'running' forever, blocking new triggers via the
        # "already running" guard. On startup, mark any orphaned 'running'
        # rows as 'failed' and bounce 'downloading' folders back to 'pending'
        # so the user can just hit Download again — the file-level downloader
        # is idempotent (skips files already on disk, resumes partials).
        if not _should_run_startup_hook():
            return
        try:
            from .models import TraxDBOperation, ScrapedFolder
            stale_ops = TraxDBOperation.objects.filter(status__in=['running', 'pending'])
            n_ops = stale_ops.update(
                status='failed',
                error_message='Worker thread killed by container restart',
            )
            n_folders = ScrapedFolder.objects.filter(download_status='downloading').update(
                download_status='pending',
            )
            if n_ops or n_folders:
                logger.warning(
                    f'TraxDB startup cleanup: marked {n_ops} stale op(s) failed, '
                    f'reset {n_folders} downloading folder(s) to pending.'
                )
        except (OperationalError, ProgrammingError):
            # Tables don't exist yet (e.g. first run before migrate) — skip silently.
            pass


def _should_run_startup_hook():
    """Run the cleanup once per real process — not from manage.py shell, not
    from migrate, not from the runserver autoreloader's outer process.
    """
    # Skip during management commands that aren't a server (migrate, shell, etc.).
    argv = sys.argv
    if len(argv) > 1 and argv[1] not in ('runserver', 'gunicorn'):
        return False
    # runserver forks an autoreloader parent — RUN_MAIN=true marks the inner
    # process. If RUN_MAIN is unset entirely we're not under the reloader, so
    # also run.
    run_main = os.environ.get('RUN_MAIN')
    if run_main is not None and run_main != 'true':
        return False
    return True
