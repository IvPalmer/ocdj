import time
import logging
import threading
import requests
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, action
from rest_framework.response import Response
from django.utils import timezone
from django import db

from wanted.models import WantedItem
from .models import SearchQueueItem, SearchResult, Download, QualityPreset
from .serializers import (
    SearchQueueItemSerializer, AddToQueueSerializer,
    SearchResultSerializer, DownloadSerializer, QualityPresetSerializer,
    SearchRequestSerializer, DownloadRequestSerializer,
)
from .services import SlskdClient, generate_queries, score_result, filter_results, extract_file_info

logger = logging.getLogger(__name__)


# ── Background search worker ────────────────────────────────

def _run_search_for_queue_item(queue_item_id):
    """Run slskd search in background thread. Updates SearchQueueItem status when done."""
    try:
        qi = SearchQueueItem.objects.get(id=queue_item_id)

        # Build query
        if qi.raw_query:
            queries = [qi.raw_query]
        else:
            queries = generate_queries(
                qi.artist, qi.title,
                release_name=qi.release_name,
                catalog_number=qi.catalog_number,
                label=qi.label,
            )
        if not queries:
            qi.status = 'failed'
            qi.error_message = 'No valid search query could be generated'
            qi.save()
            return

        client = SlskdClient()
        all_results = []

        for query in queries[:2]:
            try:
                search_response = client.search(query)
                search_id = search_response.get('id')
                if not search_id:
                    continue

                # Poll for completion — slskd searchTimeout is 15s, so 30s max
                for _ in range(6):
                    time.sleep(5)
                    search_data = client.get_search(search_id, include_responses=False)
                    state = search_data.get('state', '')
                    if 'Completed' in state:
                        break

                # Extract results
                search_data = client.get_search(search_id)
                for response in search_data.get('responses', []):
                    username = response.get('username', '')
                    for file_data in response.get('files', []):
                        file_data['_username'] = username
                        file_data['_upload_speed'] = response.get('uploadSpeed', 0)
                        file_data['_free_slots'] = response.get('hasFreeUploadSlot', False)
                        file_data['_queue_length'] = response.get('queueLength', 0)
                        all_results.append(file_data)

                try:
                    client.delete_search(search_id)
                except Exception:
                    pass

                if len(all_results) > 10:
                    break

            except Exception as e:
                logger.error(f"Search failed for query '{query}': {e}")
                continue

        # Filter + score
        all_results = filter_results(all_results)

        # For scoring, use raw_query as both artist/title fallback
        score_artist = qi.artist or ''
        score_title = qi.title or qi.raw_query or ''

        scored = []
        for result in all_results:
            score = score_result(
                score_artist, score_title, result.get('filename', ''),
                release_name=qi.release_name, catalog_number=qi.catalog_number,
            )
            if score > 30:
                scored.append({
                    'username': result.get('_username', ''),
                    'filename': result.get('filename', ''),
                    'size': result.get('size', 0),
                    'bitrate': result.get('bitRate'),
                    'bit_depth': result.get('bitDepth'),
                    'sample_rate': result.get('sampleRate'),
                    'length': result.get('length'),
                    'extension': extract_file_info(result.get('filename', ''))['extension'],
                    'match_score': round(score, 1),
                    'upload_speed': result.get('_upload_speed', 0),
                    'free_slots': result.get('_free_slots', False),
                    'queue_length': result.get('_queue_length', 0),
                })

        scored.sort(key=lambda x: x['match_score'], reverse=True)

        # Store results — delete old ones first
        SearchResult.objects.filter(queue_item=qi).delete()
        for r in scored[:50]:
            SearchResult.objects.create(
                queue_item=qi,
                wanted_item=qi.wanted_item,  # also link to wanted item if present
                username=r['username'],
                filename=r['filename'],
                file_size=r['size'],
                file_extension=r['extension'],
                bitrate=r.get('bitrate'),
                bit_depth=r.get('bit_depth'),
                sample_rate=r.get('sample_rate'),
                match_score=r['match_score'],
                upload_speed=r.get('upload_speed'),
                queue_length=r.get('queue_length'),
                free_upload_slots=r.get('free_slots', False),
            )

        # Update queue item
        qi.refresh_from_db()
        qi.search_count += 1
        qi.last_searched = timezone.now()
        if scored:
            qi.best_match_score = scored[0]['match_score']
            qi.status = 'found'
        else:
            qi.best_match_score = 0
            qi.status = 'not_found'
        qi.save()

        # Also update linked WantedItem if present
        if qi.wanted_item:
            try:
                wi = qi.wanted_item
                wi.search_count = qi.search_count
                wi.last_searched = qi.last_searched
                wi.best_match_score = qi.best_match_score
                wi.status = 'found' if scored else 'not_found'
                wi.save()
            except Exception:
                pass

        logger.info(f"Search complete for '{qi}': {len(scored)} results")

    except Exception as e:
        logger.error(f"Background search failed for queue item {queue_item_id}: {e}")
        try:
            qi = SearchQueueItem.objects.get(id=queue_item_id)
            qi.status = 'failed'
            qi.error_message = str(e)[:500]
            qi.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()


# ── Search Queue ViewSet ─────────────────────────────────────

class SearchQueueViewSet(viewsets.ModelViewSet):
    """CRUD for search queue items."""
    queryset = SearchQueueItem.objects.all()
    serializer_class = SearchQueueItemSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    @action(detail=False, methods=['post'], url_path='add')
    def add_to_queue(self, request):
        """Add items to queue from wanted items or free-text query."""
        serializer = AddToQueueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        created = []

        # Add from wanted items
        wanted_ids = serializer.validated_data.get('wanted_item_ids', [])
        for wid in wanted_ids:
            try:
                wi = WantedItem.objects.get(id=wid)
            except WantedItem.DoesNotExist:
                continue

            # Skip duplicates — same wanted item already in queue and not downloaded/failed
            existing = SearchQueueItem.objects.filter(
                wanted_item=wi,
            ).exclude(status__in=['downloaded', 'failed']).first()
            if existing:
                created.append(existing)
                continue

            qi = SearchQueueItem.objects.create(
                wanted_item=wi,
                artist=wi.artist or '',
                title=wi.title or '',
                release_name=wi.release_name or '',
                catalog_number=wi.catalog_number or '',
                label=wi.label or '',
                status='pending',
            )
            created.append(qi)

        # Add from free-text query
        query = serializer.validated_data.get('query', '').strip()
        if query:
            qi = SearchQueueItem.objects.create(
                raw_query=query,
                status='pending',
            )
            created.append(qi)

        return Response(
            SearchQueueItemSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['post'], url_path='clear')
    def clear_queue(self, request):
        """Bulk clear queue items by status."""
        mode = request.data.get('mode', 'downloaded')
        if mode == 'downloaded':
            count, _ = SearchQueueItem.objects.filter(status='downloaded').delete()
        elif mode == 'not_found':
            count, _ = SearchQueueItem.objects.filter(status='not_found').delete()
        elif mode == 'failed':
            count, _ = SearchQueueItem.objects.filter(status='failed').delete()
        elif mode == 'all_done':
            count, _ = SearchQueueItem.objects.filter(
                status__in=['downloaded', 'not_found', 'failed']
            ).delete()
        else:
            return Response({'error': 'Invalid mode'}, status=400)
        return Response({'cleared': count})


# ── API Views ────────────────────────────────────────────────

@api_view(['GET'])
def slskd_health(request):
    """Check slskd connection."""
    client = SlskdClient()
    info = client.health()
    if info:
        return Response({'status': 'connected', 'info': info})
    return Response(
        {'status': 'disconnected', 'error': 'Cannot reach slskd'},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@api_view(['POST'])
def search(request):
    """
    Search slskd. All paths are now async via SearchQueueItem:
    - queue_item_id: search existing queue item
    - wanted_item_id: find-or-create queue item, then search (backward compat)
    - query: create queue item from free-text, then search
    """
    serializer = SearchRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    qi = None

    # ── Queue item search ──
    if serializer.validated_data.get('queue_item_id'):
        try:
            qi = SearchQueueItem.objects.get(id=serializer.validated_data['queue_item_id'])
        except SearchQueueItem.DoesNotExist:
            return Response({'error': 'Queue item not found'}, status=404)

    # ── Wanted item search (backward compat — find or create queue item) ──
    elif serializer.validated_data.get('wanted_item_id'):
        wanted_item_id = serializer.validated_data['wanted_item_id']
        try:
            wi = WantedItem.objects.get(id=wanted_item_id)
        except WantedItem.DoesNotExist:
            return Response({'error': 'Wanted item not found'}, status=404)

        # Find existing queue item or create one
        qi = SearchQueueItem.objects.filter(
            wanted_item=wi,
        ).exclude(status__in=['downloaded', 'failed']).first()
        if not qi:
            qi = SearchQueueItem.objects.create(
                wanted_item=wi,
                artist=wi.artist or '',
                title=wi.title or '',
                release_name=wi.release_name or '',
                catalog_number=wi.catalog_number or '',
                label=wi.label or '',
                status='pending',
            )

    # ── Free-text search → create queue item ──
    elif serializer.validated_data.get('query'):
        query_text = serializer.validated_data['query'].strip()
        if not query_text:
            return Response({'error': 'query is required'}, status=400)
        qi = SearchQueueItem.objects.create(
            raw_query=query_text,
            status='pending',
        )

    if not qi:
        return Response({'error': 'Could not resolve search target'}, status=400)

    # Skip if already searching
    if qi.status == 'searching':
        return Response({
            'status': 'already_searching',
            'queue_item_id': qi.id,
        })

    # Mark as searching
    qi.status = 'searching'
    qi.error_message = ''
    qi.save()

    # Also update linked WantedItem
    if qi.wanted_item:
        qi.wanted_item.status = 'searching'
        qi.wanted_item.error_message = ''
        qi.wanted_item.save()

    # Fire background thread
    thread = threading.Thread(
        target=_run_search_for_queue_item,
        args=(qi.id,),
        daemon=True,
    )
    thread.start()

    return Response({
        'status': 'searching',
        'queue_item_id': qi.id,
    }, status=status.HTTP_202_ACCEPTED)


@api_view(['POST'])
def download_file(request):
    """Queue a file for download via slskd."""
    serializer = DownloadRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    username = serializer.validated_data['username']
    filename = serializer.validated_data['filename']
    size = serializer.validated_data.get('size', 0)

    client = SlskdClient()
    try:
        result = client.download(username, filename, size=size)
    except requests.exceptions.HTTPError as e:
        try:
            body = e.response.text if e.response else str(e)
        except Exception:
            body = str(e)
        logger.error(f"slskd download failed for {username}: {body}")
        return Response({'error': body}, status=e.response.status_code if e.response else 500)
    except Exception as e:
        logger.error(f"Download request failed: {e}")
        return Response({'error': str(e)}, status=500)

    slskd_id = ''
    enqueued = result.get('enqueued', [])
    if enqueued:
        slskd_id = enqueued[0].get('id', '')

    failed = result.get('failed', [])
    if failed and not enqueued:
        error_msg = str(failed[0]) if failed else 'Download rejected by slskd'
        return Response({'error': error_msg}, status=400)

    # Resolve queue_item and wanted_item
    queue_item_id = serializer.validated_data.get('queue_item_id')
    wanted_item_id = serializer.validated_data.get('wanted_item_id')

    download = Download.objects.create(
        username=username,
        filename=filename,
        queue_item_id=queue_item_id,
        wanted_item_id=wanted_item_id,
        slskd_id=slskd_id,
        status='queued',
    )

    # Update queue item status
    if download.queue_item:
        download.queue_item.status = 'downloading'
        download.queue_item.save()

    # Update wanted item status
    if download.wanted_item:
        download.wanted_item.status = 'downloading'
        download.wanted_item.save()

    logger.info(f"Download queued: {filename} from {username} (slskd_id={slskd_id})")
    return Response(DownloadSerializer(download).data, status=201)


@api_view(['GET'])
def downloads_status(request):
    """
    Get download statuses merged with slskd transfer progress.
    Returns enriched download objects with percent, speed, state from slskd.
    Also returns a set of downloading/downloaded filenames for UI indicators.
    """
    client = SlskdClient()

    # Build lookup: (username, filename) -> transfer data from slskd
    slskd_transfer_map = {}
    try:
        slskd_downloads = client.get_downloads()
        for user_entry in slskd_downloads:
            username = user_entry.get('username', '')
            for directory in user_entry.get('directories', []):
                for transfer in directory.get('files', []):
                    fn = transfer.get('filename', '')
                    key = (username.lower(), fn.lower())
                    slskd_transfer_map[key] = transfer
    except Exception as e:
        logger.warning(f"Could not fetch slskd downloads: {e}")

    our_downloads = Download.objects.all().order_by('-started')[:50]

    enriched = []
    for dl in our_downloads:
        data = DownloadSerializer(dl).data

        key = (dl.username.lower(), dl.filename.lower())
        transfer = slskd_transfer_map.get(key)

        if transfer:
            state = transfer.get('state', '')
            data['slskd_state'] = state
            data['percent'] = transfer.get('percentComplete', 0)
            data['speed'] = transfer.get('averageSpeed', 0)
            data['bytes_transferred'] = transfer.get('bytesTransferred', 0)
            data['bytes_remaining'] = transfer.get('bytesRemaining', 0)
            data['elapsed'] = transfer.get('elapsedTime', '')
            data['remaining'] = transfer.get('remainingTime', '')
            data['slskd_transfer_id'] = transfer.get('id', '')

            if 'Completed' in state and 'Succeeded' in state and dl.status != 'completed':
                dl.status = 'completed'
                dl.progress = 100
                dl.completed_at = timezone.now()
                dl.save()
                data['status'] = 'completed'
                data['progress'] = 100
                # Auto-trigger organize pipeline
                try:
                    from organize.services.pipeline import auto_ingest_download
                    import threading
                    threading.Thread(target=auto_ingest_download, args=(dl.id,), daemon=True).start()
                except Exception:
                    pass
            elif 'InProgress' in state and dl.status != 'downloading':
                dl.status = 'downloading'
                dl.progress = transfer.get('percentComplete', 0)
                dl.save()
                data['status'] = 'downloading'
            elif 'Completed' in state and ('Cancelled' in state or 'Errored' in state or 'TimedOut' in state):
                if dl.status not in ('failed', 'cancelled'):
                    dl.status = 'failed' if 'Errored' in state or 'TimedOut' in state else 'cancelled'
                    dl.error_message = state
                    dl.save()
                    data['status'] = dl.status
        else:
            data['slskd_state'] = ''
            data['percent'] = dl.progress
            data['speed'] = 0
            data['bytes_transferred'] = 0
            data['bytes_remaining'] = 0
            data['elapsed'] = ''
            data['remaining'] = ''
            data['slskd_transfer_id'] = ''

        enriched.append(data)

    # Auto-sync SearchQueueItem status
    queue_ids_seen = set()
    for dl in our_downloads:
        if dl.queue_item_id and dl.queue_item_id not in queue_ids_seen:
            queue_ids_seen.add(dl.queue_item_id)
            try:
                qi = SearchQueueItem.objects.get(id=dl.queue_item_id)
                if qi.status == 'downloading':
                    item_downloads = Download.objects.filter(queue_item=qi)
                    all_done = item_downloads.exists() and not item_downloads.exclude(
                        status__in=['completed', 'failed', 'cancelled']
                    ).exists()
                    any_completed = item_downloads.filter(status='completed').exists()
                    if all_done and any_completed:
                        qi.status = 'downloaded'
                        qi.save()
                        logger.info(f"QueueItem {qi.id} '{qi}' auto-synced to 'downloaded'")
            except SearchQueueItem.DoesNotExist:
                pass

    # Auto-sync WantedItem status
    wanted_ids_seen = set()
    for dl in our_downloads:
        if dl.wanted_item_id and dl.wanted_item_id not in wanted_ids_seen:
            wanted_ids_seen.add(dl.wanted_item_id)
            try:
                wi = WantedItem.objects.get(id=dl.wanted_item_id)
                if wi.status == 'downloading':
                    item_downloads = Download.objects.filter(wanted_item=wi)
                    all_done = item_downloads.exists() and not item_downloads.exclude(
                        status__in=['completed', 'failed', 'cancelled']
                    ).exists()
                    any_completed = item_downloads.filter(status='completed').exists()
                    if all_done and any_completed:
                        wi.status = 'downloaded'
                        wi.save()
                        logger.info(f"WantedItem {wi.id} '{wi}' auto-synced to 'downloaded'")
            except WantedItem.DoesNotExist:
                pass

    # Download indicators for UI
    download_indicators = {}
    for dl in our_downloads:
        fn_key = dl.filename.lower()
        if dl.status in ('queued', 'downloading', 'completed'):
            download_indicators[fn_key] = dl.status

    return Response({
        'downloads': enriched,
        'download_indicators': download_indicators,
    })


@api_view(['POST'])
def cancel_download(request):
    """Cancel a download by our DB id."""
    dl_id = request.data.get('download_id')
    if not dl_id:
        return Response({'error': 'download_id required'}, status=400)

    try:
        dl = Download.objects.get(id=dl_id)
    except Download.DoesNotExist:
        return Response({'error': 'Download not found'}, status=404)

    if dl.slskd_id and dl.status in ('queued', 'downloading'):
        client = SlskdClient()
        try:
            client.cancel_download(dl.username, dl.slskd_id, remove=True)
        except Exception as e:
            logger.warning(f"Could not cancel slskd transfer: {e}")

    dl.status = 'cancelled'
    dl.save()

    if dl.queue_item and dl.queue_item.status == 'downloading':
        dl.queue_item.status = 'found'
        dl.queue_item.save()

    if dl.wanted_item and dl.wanted_item.status == 'downloading':
        dl.wanted_item.status = 'found'
        dl.wanted_item.save()

    return Response({'status': 'cancelled', 'id': dl.id})


@api_view(['POST'])
def clear_downloads(request):
    """Clear completed/cancelled/failed downloads from our DB."""
    mode = request.data.get('mode', 'completed')

    if mode == 'all':
        count, _ = Download.objects.exclude(status__in=['queued', 'downloading']).delete()
    elif mode == 'completed':
        count, _ = Download.objects.filter(status='completed').delete()
    elif mode == 'failed':
        count, _ = Download.objects.filter(status__in=['failed', 'cancelled']).delete()
    else:
        return Response({'error': 'Invalid mode'}, status=400)

    return Response({'cleared': count})


@api_view(['GET'])
def search_results(request):
    """Get search results for a queue item (or wanted item for backward compat)."""
    queue_item_id = request.query_params.get('queue_item_id')
    wanted_item_id = request.query_params.get('wanted_item_id')

    if queue_item_id:
        results = SearchResult.objects.filter(queue_item_id=queue_item_id)
    elif wanted_item_id:
        results = SearchResult.objects.filter(wanted_item_id=wanted_item_id)
    else:
        return Response({'error': 'queue_item_id or wanted_item_id is required'}, status=400)

    return Response(SearchResultSerializer(results, many=True).data)


@api_view(['GET'])
def recent_searches(request):
    """Get recently searched queue items with result counts."""
    items = SearchQueueItem.objects.filter(
        last_searched__isnull=False,
    ).order_by('-last_searched')[:20]

    return Response(SearchQueueItemSerializer(items, many=True).data)


class QualityPresetViewSet(viewsets.ModelViewSet):
    queryset = QualityPreset.objects.all()
    serializer_class = QualityPresetSerializer
