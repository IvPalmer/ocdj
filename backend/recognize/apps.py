import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class RecognizeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'recognize'

    def ready(self):
        # Auto-resume stale jobs on startup
        if 'runserver' not in sys.argv:
            return
        # With --noreload: RUN_MAIN isn't set, run directly
        # With reloader: only run in the child process (RUN_MAIN=true)
        is_reloader = os.environ.get('RUN_MAIN')
        is_noreload = '--noreload' in sys.argv
        if not is_noreload and not is_reloader:
            return

        import threading
        threading.Timer(5.0, self._resume_stale).start()

    @staticmethod
    def _resume_stale():
        try:
            from .services.pipeline import resume_stale_jobs
            resume_stale_jobs()
        except Exception as e:
            print(f'[recognize] Failed to resume stale jobs: {e}')
