"""Huey tasks for TraxDB long-running ops.

The `run_sync`, `run_download`, `run_audit` service functions are
long-lived (sync can take minutes, download can take hours). Previously
each was spawned as `threading.Thread(daemon=True)` from the view — any
backend container restart lost the work in-flight. Routing them through
Huey lets the worker container outlive a backend redeploy.
"""
import logging

from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task, lock_task

from traxdb.services import run_sync, run_download, run_audit

logger = logging.getLogger(__name__)


@db_task(retries=0)
def task_sync(operation_id: int, max_pages: int | None = None):
    with lock_task('traxdb-sync'):
        run_sync(operation_id, max_pages=max_pages)


@db_task(retries=0)
def task_download(operation_id: int, sync_report_path: str | None = None,
                  links_key: str = 'links_new'):
    with lock_task('traxdb-download'):
        run_download(operation_id, sync_report_path=sync_report_path, links_key=links_key)


@db_task(retries=0)
def task_audit(operation_id: int, sync_report_path: str | None = None):
    with lock_task('traxdb-audit'):
        run_audit(operation_id, sync_report_path=sync_report_path)


# Pixeldrain API keys expire 30 days after their LAST USE, not creation.
# Weeks can pass between manual syncs, so without a heartbeat the key dies
# silently and the next download run 401s. Twice a month is comfortably
# inside the 30-day idle window while staying negligible traffic-wise.
@db_periodic_task(crontab(day='1,15', hour='6', minute='0'), retries=0)
def task_pixeldrain_keepalive():
    with lock_task('traxdb-pixeldrain-keepalive'):
        from core.services.config import get_config
        from traxdb.models import ScrapedFolder
        from traxdb.services.pixeldrain import PixeldrainClient, PixeldrainError

        api_key = get_config('PIXELDRAIN_API_KEY')
        if not api_key:
            logger.warning('pixeldrain keepalive: no PIXELDRAIN_API_KEY configured, skipping')
            return

        client = PixeldrainClient(api_key=api_key)
        folder = (
            ScrapedFolder.objects.exclude(pixeldrain_url='')
            .order_by('-id')
            .first()
        )
        try:
            if folder:
                list_id = PixeldrainClient.parse_list_id(folder.pixeldrain_url)
                data = client.get_list(list_id)
                logger.info(
                    'pixeldrain keepalive OK: list %s, %d files',
                    list_id, len(data.get('files') or []),
                )
            else:
                # No scraped folders yet — an authenticated no-op still
                # counts as key usage.
                r = client._session.get(
                    'https://pixeldrain.com/api/user', timeout=30,
                )
                r.raise_for_status()
                logger.info('pixeldrain keepalive OK: /user status %d', r.status_code)
        except Exception as e:
            if '401' in str(e) or 'authentication_failed' in str(e):
                logger.error(
                    'pixeldrain keepalive FAILED — API key is dead or revoked. '
                    'Renew at pixeldrain.com/user/api_keys (magic-link login '
                    'with the operator email) and update PIXELDRAIN_API_KEY '
                    'in config. Error: %s', e,
                )
            else:
                logger.error('pixeldrain keepalive error (non-auth): %s', e)
            raise
