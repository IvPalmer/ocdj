"""Data migration: backfill SearchQueueItem from existing WantedItems that have SearchResults."""

from django.db import migrations


def backfill_queue_items(apps, schema_editor):
    WantedItem = apps.get_model('wanted', 'WantedItem')
    SearchQueueItem = apps.get_model('soulseek', 'SearchQueueItem')
    SearchResult = apps.get_model('soulseek', 'SearchResult')
    Download = apps.get_model('soulseek', 'Download')

    # For each WantedItem that has search results or downloads, create a SearchQueueItem
    wanted_ids_with_results = set(
        SearchResult.objects.values_list('wanted_item_id', flat=True).distinct()
    )
    wanted_ids_with_downloads = set(
        Download.objects.filter(wanted_item__isnull=False)
        .values_list('wanted_item_id', flat=True)
        .distinct()
    )
    all_wanted_ids = wanted_ids_with_results | wanted_ids_with_downloads

    for wi in WantedItem.objects.filter(id__in=all_wanted_ids):
        # Map WantedItem status to SearchQueueItem status
        status_map = {
            'pending': 'pending',
            'identified': 'pending',
            'searching': 'searching',
            'found': 'found',
            'downloading': 'downloading',
            'downloaded': 'downloaded',
            'tagged': 'downloaded',
            'organized': 'downloaded',
            'not_found': 'not_found',
            'failed': 'failed',
        }
        queue_status = status_map.get(wi.status, 'pending')

        qi = SearchQueueItem.objects.create(
            wanted_item=wi,
            artist=wi.artist or '',
            title=wi.title or '',
            release_name=wi.release_name or '',
            catalog_number=wi.catalog_number or '',
            label=wi.label or '',
            status=queue_status,
            search_count=wi.search_count,
            last_searched=wi.last_searched,
            best_match_score=wi.best_match_score,
        )

        # Re-link search results to the queue item
        SearchResult.objects.filter(wanted_item=wi).update(queue_item=qi)

        # Re-link downloads to the queue item
        Download.objects.filter(wanted_item=wi).update(queue_item=qi)


def reverse_backfill(apps, schema_editor):
    """Reverse: just clear queue_item FKs (data stays)."""
    SearchResult = apps.get_model('soulseek', 'SearchResult')
    Download = apps.get_model('soulseek', 'Download')
    SearchQueueItem = apps.get_model('soulseek', 'SearchQueueItem')

    SearchResult.objects.all().update(queue_item=None)
    Download.objects.all().update(queue_item=None)
    SearchQueueItem.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('soulseek', '0002_add_search_queue_item'),
        ('wanted', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill_queue_items, reverse_backfill),
    ]
