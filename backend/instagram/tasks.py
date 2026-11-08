"""Retention uses the existing Huey worker; no new Mac service or login."""
from huey import crontab
from huey.contrib.djhuey import db_periodic_task
from .cache import cleanup


@db_periodic_task(crontab(minute='*/30'))
def cleanup_instagram_cache():
    cleanup()
