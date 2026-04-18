"""Huey tasks for the Recognize module.

These replace the raw `threading.Thread` launches inside
`recognize.services.pipeline`. Enqueuing a task writes a row into
`/app/huey/ocdj_huey.sqlite3`; the worker container claims it and runs
`_recognize_worker`. If the worker dies mid-task, the row stays claimed
until Huey's visibility timeout elapses — at which point it's picked up
again. Combined with the module's existing resume-from-raw_results
semantics this gives end-to-end restart-safety.
"""
from huey.contrib.djhuey import db_task, lock_task

from recognize.services.pipeline import _recognize_worker


@db_task(retries=0, retry_delay=60)
def recognize_job(job_id: int):
    # Per-job lock so duplicate enqueues don't produce two concurrent workers
    # (the module's in-process _active_jobs set doesn't protect across the
    # backend<->worker container boundary).
    with lock_task(f'recognize-job-{job_id}'):
        _recognize_worker(job_id)
