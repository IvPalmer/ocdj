import logging
import os

from django.db.models import Q
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task, lock_task
import requests

from soulseek.lifecycle import ACTIVE, reconcile_downloads, sync_linked_items, transfer_entries
from soulseek.models import Download
from soulseek.services import SlskdClient
from .models import PipelineItem
from .services.pipeline import (
    ACTIVE_STAGE_TO_START, discover_and_ingest, process_pipeline_item, scan_settled_inbox,
)

logger = logging.getLogger(__name__)


@db_task()
def ingest_soulseek_download(download_id):
    discover_and_ingest(download_id, require_staged=True)


@db_task()
def process_item(item_id, force_retry=False):
    process_pipeline_item(item_id, force_retry=force_retry)


@db_periodic_task(crontab(minute='*'))
@lock_task('organize-reconcile')
def recover_pipeline():
    """Recover work even if no browser is polling or a dispatch was lost."""
    if Download.objects.filter(status__in=ACTIVE).exists():
        try:
            entries = transfer_entries(SlskdClient().get_downloads())
        except requests.RequestException:
            logger.warning('Could not refresh Soulseek transfers for Organize', exc_info=True)
        else:
            reconcile_downloads(entries)
            sync_linked_items(entries)

    pending_downloads = Download.objects.filter(
        status='completed', completed_size__gt=0, pipeline_items__isnull=True,
    ).order_by('-completed_at').values_list('pk', flat=True)[:25]
    for download_id in list(pending_downloads):
        try:
            discover_and_ingest(download_id, require_staged=True)
        except Exception:
            logger.exception('Could not ingest completed download %s', download_id)

    try:
        scan_settled_inbox()
    except Exception:
        logger.exception('Could not scan the Organize inbox')

    stages = ['downloaded', 'tagged', 'renamed', 'converted', *ACTIVE_STAGE_TO_START]
    if os.environ.get('OCDJ_AUTOPUBLISH') == '1':
        stages.append('ready')
    work = PipelineItem.objects.filter(archive_state='on_workbench').filter(
        Q(stage__in=stages)
        | Q(stage='failed', retry_after__lte=timezone.now())
        | Q(stage='failed', retry_from=''),
    ).order_by('updated', 'pk').values_list('pk', flat=True)[:10]
    for item_id in list(work):
        try:
            process_pipeline_item(item_id)
        except Exception:
            logger.exception('Could not process pipeline item %s', item_id)
