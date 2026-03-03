import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class RecognizeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'recognize'

    def ready(self):
        # Auto-resume stale jobs on startup — only in the reloader's child process
        if 'runserver' not in sys.argv:
            return
        if not os.environ.get('RUN_MAIN'):
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
