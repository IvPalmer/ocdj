"""
End-to-end pipeline automation for OCDJ.

Orchestrates the flow: Wanted -> Search -> Download -> Organize
Each step is independently toggleable via Config keys.
"""
import logging
import threading
from django.db.models import Q
from django.utils import timezone
from django import db

from core.models import Config

logger = logging.getLogger(__name__)

# Config keys and defaults
AUTOMATION_DEFAULTS = {
    'AUTOMATION_ENABLED': 'false',
    'AUTOMATION_AUTO_SEARCH': 'false',
    'AUTOMATION_AUTO_DOWNLOAD': 'false',
    'AUTOMATION_CONFIDENCE_THRESHOLD': '85',
    'AUTOMATION_AUTO_ORGANIZE': 'false',
}


def get_automation_config():
    """Read all automation config values, falling back to defaults."""
    config = {}
    for key, default in AUTOMATION_DEFAULTS.items():
        try:
            val = Config.objects.get(key=key).value
        except Config.DoesNotExist:
            val = default
        if default in ('true', 'false'):
            config[key] = val.lower() == 'true'
        else:
            try:
                config[key] = int(val)
            except (ValueError, TypeError):
                config[key] = int(default)
    return config


def set_automation_config(updates):
    """Update automation config values."""
    updated = []
    for key, value in updates.items():
        if key not in AUTOMATION_DEFAULTS:
            continue
        str_val = str(value).lower() if isinstance(value, bool) else str(value)
        Config.objects.update_or_create(key=key, defaults={'value': str_val})
        updated.append(key)
    return updated


def run_automation_cycle(dry_run=False):
    """
    Run one full automation cycle. Idempotent — safe to call repeatedly.

    Returns a dict describing what happened at each step.
    """
    config = get_automation_config()
    report = {
        'enabled': config['AUTOMATION_ENABLED'],
        'timestamp': timezone.now().isoformat(),
        'dry_run': dry_run,
        'steps': {
            'search': {'skipped': True, 'reason': 'disabled'},
            'download': {'skipped': True, 'reason': 'disabled'},
            'organize': {'skipped': True, 'reason': 'disabled'},
        },
    }

    if not config['AUTOMATION_ENABLED']:
        report['steps'] = {
            'search': {'skipped': True, 'reason': 'automation disabled'},
            'download': {'skipped': True, 'reason': 'automation disabled'},
            'organize': {'skipped': True, 'reason': 'automation disabled'},
        }
        return report

    # Step 1: Auto-search pending wanted items
    if config['AUTOMATION_AUTO_SEARCH']:
        report['steps']['search'] = _step_auto_search(dry_run)

    # Step 2: Auto-download high-confidence matches
    if config['AUTOMATION_AUTO_DOWNLOAD']:
        threshold = config['AUTOMATION_CONFIDENCE_THRESHOLD']
        report['steps']['download'] = _step_auto_download(dry_run, threshold)

    # Step 3: Auto-organize completed downloads
    if config['AUTOMATION_AUTO_ORGANIZE']:
        report['steps']['organize'] = _step_auto_organize(dry_run)

    return report


def _step_auto_search(dry_run):
    """Find pending wanted items with searchable metadata, create queue items."""
    from wanted.models import WantedItem
    from soulseek.models import SearchQueueItem

    items = WantedItem.objects.filter(
        status='pending',
    ).filter(
        Q(artist__gt='', title__gt='') | Q(catalog_number__gt='')
    )

    queued = []
    skipped = []

    for item in items:
        # Skip if already has an active queue item
        existing = SearchQueueItem.objects.filter(
            wanted_item=item,
        ).exclude(status__in=['downloaded', 'failed']).exists()

        if existing:
            skipped.append({'id': item.id, 'label': str(item), 'reason': 'already queued'})
            continue

        if dry_run:
            queued.append({'id': item.id, 'label': str(item)})
            continue

        SearchQueueItem.objects.create(
            wanted_item=item,
            artist=item.artist,
            title=item.title,
            release_name=item.release_name,
            catalog_number=item.catalog_number,
            label=item.label,
            status='pending',
        )
        item.status = 'searching'
        item.save(update_fields=['status'])
        queued.append({'id': item.id, 'label': str(item)})
        logger.info(f"[automation] Queued search for: {item}")

    return {
        'skipped': False,
        'queued': len(queued),
        'already_queued': len(skipped),
        'items': queued,
    }


def _step_auto_download(dry_run, threshold):
    """Find queue items with results above confidence threshold, trigger download."""
    from soulseek.models import SearchQueueItem, SearchResult, Download
    from soulseek.services import SlskdClient

    # Find queue items that have search results but haven't been downloaded
    queue_items = SearchQueueItem.objects.filter(status='found')

    downloaded = []
    skipped = []

    for qi in queue_items:
        # Get best result above threshold
        best = SearchResult.objects.filter(
            queue_item=qi,
            match_score__gte=threshold,
        ).order_by('-match_score').first()

        if not best:
            skipped.append({
                'id': qi.id,
                'label': str(qi),
                'reason': f'no results above {threshold}%',
                'best_score': qi.best_match_score,
            })
            continue

        # Check if already downloading/downloaded
        existing_dl = Download.objects.filter(
            queue_item=qi,
            status__in=['queued', 'downloading', 'completed'],
        ).exists()
        if existing_dl:
            skipped.append({
                'id': qi.id,
                'label': str(qi),
                'reason': 'already downloading/downloaded',
            })
            continue

        logger.info(
            f"[automation] Auto-downloading for '{qi}': "
            f"{best.filename} from {best.username} "
            f"(score: {best.match_score}%, threshold: {threshold}%)"
        )

        if dry_run:
            downloaded.append({
                'id': qi.id,
                'label': str(qi),
                'file': best.filename,
                'username': best.username,
                'score': best.match_score,
            })
            continue

        try:
            client = SlskdClient()
            result = client.download(best.username, best.filename, size=best.file_size)

            slskd_id = ''
            enqueued = result.get('enqueued', [])
            if enqueued:
                slskd_id = enqueued[0].get('id', '')

            failed = result.get('failed', [])
            if failed and not enqueued:
                skipped.append({
                    'id': qi.id,
                    'label': str(qi),
                    'reason': f'slskd rejected: {failed[0]}',
                })
                continue

            dl = Download.objects.create(
                username=best.username,
                filename=best.filename,
                queue_item=qi,
                wanted_item=qi.wanted_item,
                search_result=best,
                slskd_id=slskd_id,
                status='queued',
            )

            qi.status = 'downloading'
            qi.save(update_fields=['status'])

            if qi.wanted_item:
                qi.wanted_item.status = 'downloading'
                qi.wanted_item.save(update_fields=['status'])

            downloaded.append({
                'id': qi.id,
                'label': str(qi),
                'file': best.filename,
                'username': best.username,
                'score': best.match_score,
                'download_id': dl.id,
            })

        except Exception as e:
            logger.error(f"[automation] Download failed for '{qi}': {e}")
            skipped.append({
                'id': qi.id,
                'label': str(qi),
                'reason': f'download error: {e}',
            })

    return {
        'skipped': False,
        'downloaded': len(downloaded),
        'below_threshold': len(skipped),
        'threshold': threshold,
        'items': downloaded,
    }


def _step_auto_organize(dry_run):
    """Find completed downloads not yet in pipeline, ingest and process."""
    from soulseek.models import Download
    from organize.models import PipelineItem
    from organize.services.pipeline import discover_and_ingest, process_pipeline_item

    # Completed downloads that don't have pipeline items yet
    completed = Download.objects.filter(status='completed').exclude(
        id__in=PipelineItem.objects.values_list('download_id', flat=True)
    )

    ingested = []
    failed = []

    for dl in completed:
        if dry_run:
            ingested.append({'id': dl.id, 'file': dl.filename})
            continue

        try:
            item = discover_and_ingest(dl.id)
            if item:
                # Process through pipeline in background
                threading.Thread(
                    target=_safe_process,
                    args=(item.id,),
                    daemon=True,
                ).start()
                ingested.append({
                    'id': dl.id,
                    'file': dl.filename,
                    'pipeline_item_id': item.id,
                })
                logger.info(f"[automation] Ingested and processing: {dl.filename}")
            else:
                failed.append({
                    'id': dl.id,
                    'file': dl.filename,
                    'reason': 'file not found on disk',
                })
        except Exception as e:
            logger.error(f"[automation] Organize failed for download {dl.id}: {e}")
            failed.append({
                'id': dl.id,
                'file': dl.filename,
                'reason': str(e),
            })

    return {
        'skipped': False,
        'ingested': len(ingested),
        'failed': len(failed),
        'items': ingested,
    }


def _safe_process(item_id):
    """Process pipeline item with proper DB cleanup."""
    try:
        from organize.services.pipeline import process_pipeline_item
        process_pipeline_item(item_id)
    except Exception as e:
        logger.error(f"[automation] Pipeline processing failed for item {item_id}: {e}")
    finally:
        db.connections.close_all()


def get_pipeline_status():
    """Get counts at each stage of the full pipeline flow."""
    from wanted.models import WantedItem
    from soulseek.models import SearchQueueItem, Download
    from organize.models import PipelineItem
    from django.db.models import Count

    wanted_counts = dict(
        WantedItem.objects.values_list('status')
        .annotate(count=Count('id'))
        .values_list('status', 'count')
    )

    queue_counts = dict(
        SearchQueueItem.objects.values_list('status')
        .annotate(count=Count('id'))
        .values_list('status', 'count')
    )

    download_counts = dict(
        Download.objects.values_list('status')
        .annotate(count=Count('id'))
        .values_list('status', 'count')
    )

    pipeline_counts = dict(
        PipelineItem.objects.values_list('stage')
        .annotate(count=Count('id'))
        .values_list('stage', 'count')
    )

    return {
        'wanted_pending': wanted_counts.get('pending', 0),
        'searching': queue_counts.get('pending', 0) + queue_counts.get('searching', 0),
        'found': queue_counts.get('found', 0),
        'downloading': download_counts.get('queued', 0) + download_counts.get('downloading', 0),
        'downloaded': download_counts.get('completed', 0),
        'organizing': pipeline_counts.get('downloaded', 0) + pipeline_counts.get('tagging', 0) + pipeline_counts.get('tagged', 0) + pipeline_counts.get('renaming', 0) + pipeline_counts.get('renamed', 0),
        'ready': pipeline_counts.get('ready', 0),
        'failed': (
            wanted_counts.get('failed', 0)
            + queue_counts.get('failed', 0)
            + download_counts.get('failed', 0)
            + pipeline_counts.get('failed', 0)
        ),
    }
