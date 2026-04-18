"""Huey tasks for TraxDB long-running ops.

The `run_sync`, `run_download`, `run_audit` service functions are
long-lived (sync can take minutes, download can take hours). Previously
each was spawned as `threading.Thread(daemon=True)` from the view — any
backend container restart lost the work in-flight. Routing them through
Huey lets the worker container outlive a backend redeploy.
"""
from huey.contrib.djhuey import db_task, lock_task

from traxdb.services import run_sync, run_download, run_audit


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
