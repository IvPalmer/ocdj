"""Huey tasks for the Shazam feed.

Server-side on purpose. The first version of this feed read a playlist out of
Music.app on the operator's Mac, which meant it only ran while that laptop was
awake — and, worse, put unvetted club IDs into the finished library. Polling
Spotify from the VPS has neither problem.
"""
import logging

from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task, lock_task

logger = logging.getLogger(__name__)


@db_task(retries=0)
def task_shazam_sync(seed: bool = False):
    from wanted.services.shazam import sync_from_spotify
    with lock_task('shazam-sync'):
        return sync_from_spotify(seed=seed)


# Every 10 minutes: a Shazam is worth seeing while you still remember the room
# you heard it in, and a cycle is one Spotify read that usually finds nothing.
@db_periodic_task(crontab(minute='*/10'), retries=0)
def task_shazam_poll():
    from core.services.config import get_config
    from wanted.services.shazam import sync_from_spotify

    with lock_task('shazam-sync'):
        try:
            result = sync_from_spotify()
        except Exception as e:
            # Spotify's refresh token expiring is the expected failure here, and
            # it is silent by nature: nothing arrives and nothing complains.
            # The panel reads `last_checked`, which stays stale, so the operator
            # sees "overdue" rather than an empty list they mistake for calm.
            logger.error('shazam poll failed: %r', e)
            return
        if result.get('error'):
            logger.warning('shazam poll: %s', result['error'])
