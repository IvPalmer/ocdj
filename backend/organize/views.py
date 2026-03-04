import threading

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status as http_status
from django.db.models import Count

from .models import PipelineItem
from .serializers import PipelineItemSerializer


@api_view(['GET'])
def pipeline_list(request):
    """List pipeline items, optionally filtered by stage."""
    items = PipelineItem.objects.all()
    stage = request.query_params.get('stage')
    if stage:
        items = items.filter(stage=stage)

    # Simple pagination matching existing patterns
    from rest_framework.pagination import PageNumberPagination
    paginator = PageNumberPagination()
    paginator.page_size = 50
    page = paginator.paginate_queryset(items, request)
    serializer = PipelineItemSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(['GET', 'PATCH'])
def pipeline_detail(request, pk):
    """Get or update a single pipeline item."""
    try:
        item = PipelineItem.objects.get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'Not found'}, status=http_status.HTTP_404_NOT_FOUND)

    if request.method == 'PATCH':
        editable = ['artist', 'title', 'album', 'label', 'catalog_number', 'genre', 'year', 'track_number']
        updated = []
        for field in editable:
            if field in request.data:
                setattr(item, field, request.data[field])
                updated.append(field)
        if updated:
            item.metadata_source = 'manual'
            updated.append('metadata_source')
            item.save(update_fields=updated)
        return Response(PipelineItemSerializer(item).data)

    return Response(PipelineItemSerializer(item).data)


@api_view(['GET'])
def pipeline_stats(request):
    """Return counts per pipeline stage."""
    counts = dict(
        PipelineItem.objects.values_list('stage')
        .annotate(count=Count('id'))
        .values_list('stage', 'count')
    )
    return Response({
        'downloaded': counts.get('downloaded', 0),
        'tagging': counts.get('tagging', 0),
        'tagged': counts.get('tagged', 0),
        'renaming': counts.get('renaming', 0),
        'renamed': counts.get('renamed', 0),
        'converting': counts.get('converting', 0),
        'converted': counts.get('converted', 0),
        'ready': counts.get('ready', 0),
        'failed': counts.get('failed', 0),
        'total': sum(counts.values()),
    })


@api_view(['POST'])
def pipeline_process_all(request):
    """Process all items in 'downloaded' stage through the pipeline."""
    from .services.pipeline import process_all_pending
    items = PipelineItem.objects.filter(stage='downloaded')
    count = items.count()
    if count == 0:
        return Response({'message': 'No items to process', 'count': 0})

    threading.Thread(target=process_all_pending, daemon=True).start()
    return Response({'message': f'Processing {count} items', 'count': count})


@api_view(['POST'])
def pipeline_process_single(request, pk):
    """Process a single pipeline item."""
    try:
        item = PipelineItem.objects.get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'Not found'}, status=http_status.HTTP_404_NOT_FOUND)

    from .services.pipeline import process_pipeline_item
    threading.Thread(target=process_pipeline_item, args=(item.id,), daemon=True).start()
    return Response({'message': f'Processing item {item.id}'})


@api_view(['POST'])
def pipeline_retry(request, pk):
    """Retry a failed item — reset to last good stage and re-process."""
    try:
        item = PipelineItem.objects.get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'Not found'}, status=http_status.HTTP_404_NOT_FOUND)

    if item.stage != 'failed':
        return Response({'error': 'Item is not in failed state'}, status=http_status.HTTP_400_BAD_REQUEST)

    item.stage = 'downloaded'
    item.error_message = ''
    item.save()

    from .services.pipeline import process_pipeline_item
    threading.Thread(target=process_pipeline_item, args=(item.id,), daemon=True).start()
    return Response({'message': f'Retrying item {item.id}'})


@api_view(['POST'])
def pipeline_skip(request, pk):
    """Skip current stage — advance to the next one."""
    try:
        item = PipelineItem.objects.get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'Not found'}, status=http_status.HTTP_404_NOT_FOUND)

    STAGE_ORDER = ['downloaded', 'tagged', 'renamed', 'converted', 'ready']
    current_base = item.stage.replace('ing', 'ed') if item.stage.endswith('ing') else item.stage
    if current_base in STAGE_ORDER:
        idx = STAGE_ORDER.index(current_base)
        if idx < len(STAGE_ORDER) - 1:
            item.stage = STAGE_ORDER[idx + 1]
            item.save()
            return Response({'message': f'Skipped to {item.stage}', 'stage': item.stage})

    return Response({'error': 'Cannot skip from this stage'}, status=http_status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def pipeline_retag(request, pk):
    """Re-write audio tags from current metadata (after manual edit)."""
    try:
        item = PipelineItem.objects.get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'Not found'}, status=http_status.HTTP_404_NOT_FOUND)

    from .services.tagger import write_tags
    from .services.renamer import rename_file

    metadata = {
        'artist': item.artist, 'title': item.title, 'album': item.album,
        'label': item.label, 'catalog_number': item.catalog_number,
        'genre': item.genre, 'year': item.year, 'track_number': item.track_number,
    }
    try:
        write_tags(item.current_path, metadata)
        rename_file(item)
        return Response(PipelineItemSerializer(item).data)
    except Exception as e:
        return Response({'error': str(e)}, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def pipeline_scan(request):
    """Scan completed downloads and create PipelineItems for any not yet tracked."""
    from .services.pipeline import scan_completed_downloads
    created = scan_completed_downloads()
    return Response({'message': f'Created {created} new pipeline items', 'created': created})


@api_view(['GET', 'POST'])
def conversion_rules(request):
    """Get or update format conversion rules."""
    from core.views import get_config
    from core.models import Config
    from .services.converter import DEFAULT_RULES, parse_rules

    if request.method == 'POST':
        rules_text = request.data.get('rules', '')
        # Validate rules parse correctly
        parsed = parse_rules(rules_text)
        if not parsed and rules_text.strip():
            return Response(
                {'error': 'No valid rules could be parsed'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        Config.objects.update_or_create(
            key='ORGANIZE_CONVERSION_RULES',
            defaults={'value': rules_text},
        )
        return Response({'rules': rules_text, 'parsed_count': len(parsed)})

    # GET
    rules_text = get_config('ORGANIZE_CONVERSION_RULES') or DEFAULT_RULES
    parsed = parse_rules(rules_text)
    return Response({'rules': rules_text, 'parsed_count': len(parsed)})
