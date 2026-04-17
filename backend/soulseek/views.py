import time
import logging
import threading
import requests
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, action
from rest_framework.response import Response
from django.utils import timezone
from django.db import transaction
from django.db.models import Count
from django import db

from wanted.models import WantedItem
from .models import SearchQueueItem, SearchResult, Download, QualityPreset
from .serializers import (
    SearchQueueItemSerializer, AddToQueueSerializer,
    SearchResultSerializer, DownloadSerializer, QualityPresetSerializer,
    SearchRequestSerializer, DownloadRequestSerializer,
)
from .services import SlskdClient, generate_queries, score_result, filter_results, extract_file_info, simplify_query

logger = logging.getLogger(__name__)


# ── Background search worker ────────────────────────────────

def _run_search_for_queue_item(queue_item_id):
    """Run slskd search in background thread. Updates SearchQueueItem status when done."""
    try:
        qi = SearchQueueItem.objects.get(id=queue_item_id)

        # Build query — simplify even raw text so hyphens/accents/punctuation
        # don't tank slsk recall.
        if qi.raw_query:
            simplified = simplify_query(qi.raw_query)
            queries = [simplified] if simplified else []
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
                    if not search_data:
                        break
                    state = search_data.get('state', '')
                    if 'Completed' in state:
                        break

                # Extract results — `files` are freely available, `lockedFiles`
                # are gated to privileged peers. Keep both so the UI can show
                # the 🔒 indicator instead of hiding them silently.
                search_data = client.get_search(search_id) or {}
                for response in search_data.get('responses', []):
                    username = response.get('username', '')
                    upload_speed = response.get('uploadSpeed', 0)
                    free_slots = response.get('hasFreeUploadSlot', False)
                    queue_length = response.get('queueLength', 0)
                    for file_data in response.get('files', []):
                        file_data['_username'] = username
                        file_data['_upload_speed'] = upload_speed
                        file_data['_free_slots'] = free_slots
                        file_data['_queue_length'] = queue_length
                        file_data['_is_locked'] = False
                        all_results.append(file_data)
                    for file_data in response.get('lockedFiles', []):
                        file_data['_username'] = username
                        file_data['_upload_speed'] = upload_speed
                        file_data['_free_slots'] = free_slots
                        file_data['_queue_length'] = queue_length
                        file_data['_is_locked'] = True
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
                    'is_locked': result.get('_is_locked', False),
                })

        scored.sort(key=lambda x: x['match_score'], reverse=True)

        # Store results — delete old ones first, then bulk insert
        SearchResult.objects.filter(queue_item=qi).delete()
        SearchResult.objects.bulk_create([
            SearchResult(
                queue_item=qi,
                wanted_item=qi.wanted_item,
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
                is_locked=r.get('is_locked', False),
            )
            for r in scored[:50]
        ])

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
        qs = super().get_queryset().annotate(
            search_results_count_annotated=Count('search_results'),
        )
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

    # Mark as searching — atomic so queue/wanted status never diverge
    with transaction.atomic():
        qi.status = 'searching'
        qi.error_message = ''
        qi.save()
        if qi.wanted_item:
            qi.wanted_item.status = 'searching'
            qi.wanted_item.error_message = ''
            qi.wanted_item.save()

    # Fire background thread after commit so it sees the 'searching' row
    def _start_search():
        threading.Thread(
            target=_run_search_for_queue_item,
            args=(qi.id,),
            daemon=True,
        ).start()

    transaction.on_commit(_start_search)

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
    slskd_unreachable = False
    slskd_error = ''
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
        slskd_unreachable = True
        slskd_error = str(e)[:200]
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
                with transaction.atomic():
                    dl.status = 'completed'
                    dl.progress = 100
                    dl.completed_at = timezone.now()
                    dl.save()
                data['status'] = 'completed'
                data['progress'] = 100
                # Auto-trigger organize pipeline after commit so thread sees completed row
                dl_id = dl.id
                def _ingest(_id=dl_id):
                    try:
                        from organize.services.pipeline import auto_ingest_download
                        threading.Thread(target=auto_ingest_download, args=(_id,), daemon=True).start()
                    except Exception as exc:
                        logger.warning(f"auto_ingest dispatch failed for {_id}: {exc}")
                transaction.on_commit(_ingest)
            elif 'InProgress' in state and dl.status != 'downloading':
                dl.status = 'downloading'
                dl.progress = transfer.get('percentComplete', 0)
                dl.save()
                data['status'] = 'downloading'
            elif 'Completed' in state and (
                'Cancelled' in state or 'Errored' in state
                or 'TimedOut' in state or 'Rejected' in state
            ):
                if dl.status not in ('failed', 'cancelled'):
                    if 'Cancelled' in state:
                        dl.status = 'cancelled'
                    else:
                        # Rejected/Errored/TimedOut → genuine failure the user
                        # should see in the Failed group. Includes peers that
                        # rejected the share (privileges, disabled, banned us).
                        dl.status = 'failed'
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
        'slskd_unreachable': slskd_unreachable,
        'slskd_error': slskd_error,
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


AUDIO_EXT_SET = {'mp3', 'flac', 'wav', 'aiff', 'aif', 'ogg', 'opus', 'wma', 'aac', 'm4a'}


@api_view(['GET'])
def browse_user(request):
    """Browse a Soulseek user's shared files.

    Query params:
      username: required
      dir_prefix: optional path prefix — only directories whose name starts with
        this are returned. Slskd uses backslashes in paths; we normalise both
        sides to forward-slashes for matching so the FE can pass a clean prefix.
      audio_only: '1' to drop non-audio files (default: include everything).
      limit: max number of directories to return (default 200).
    """
    username = request.query_params.get('username', '').strip()
    if not username:
        return Response({'error': 'username is required'}, status=400)

    dir_prefix = request.query_params.get('dir_prefix', '').strip()
    audio_only = request.query_params.get('audio_only') in ('1', 'true', 'True')
    try:
        limit = int(request.query_params.get('limit', 200))
    except (TypeError, ValueError):
        limit = 200

    client = SlskdClient()
    try:
        data = client.browse_user(username)
    except requests.exceptions.HTTPError as e:
        body = ''
        try:
            body = e.response.text if e.response else ''
        except Exception:
            pass
        # slskd surfaces peer-side timeouts as a 500 with a TimeoutException
        # in the body. Translate to a user-friendly message instead of leaking
        # the .NET stack trace.
        if 'TimeoutException' in body or 'timed out' in body.lower():
            return Response(
                {'error': f'{username} did not respond (likely offline, busy, or has browsing disabled). Try again later.'},
                status=504,
            )
        msg = body[:200] or str(e)
        return Response({'error': msg}, status=e.response.status_code if e.response else 502)
    except requests.exceptions.Timeout:
        return Response(
            {'error': f'Browse timed out — {username} may be offline.'},
            status=504,
        )
    except Exception as e:
        return Response({'error': f'Browse failed: {e}'}, status=502)

    raw_dirs = data.get('directories', []) if isinstance(data, dict) else []
    raw_locked = data.get('lockedDirectories', []) if isinstance(data, dict) else []

    def _normalize(p):
        return (p or '').replace('\\', '/').lower().strip('/')

    prefix_norm = _normalize(dir_prefix)
    filtered = []
    total_files = 0

    def _process(dir_list, locked):
        nonlocal total_files
        for d in dir_list:
            name = d.get('name', '')
            if prefix_norm and not _normalize(name).startswith(prefix_norm):
                continue
            files = d.get('files', []) or []
            if audio_only:
                files = [
                    f for f in files
                    if (f.get('extension') or f.get('filename', '').rsplit('.', 1)[-1] or '').lower().lstrip('.') in AUDIO_EXT_SET
                ]
            total_files += len(files)
            filtered.append({
                'name': name,
                'file_count': len(files),
                'locked': locked,
                'files': [
                    {
                        'filename': f.get('filename', ''),
                        'size': f.get('size', 0),
                        'extension': (f.get('extension') or '').lstrip('.'),
                        'bitrate': f.get('bitRate'),
                        'sample_rate': f.get('sampleRate'),
                        'bit_depth': f.get('bitDepth'),
                        'length': f.get('length'),
                        'locked': locked,
                    }
                    for f in files
                ],
            })

    _process(raw_dirs, locked=False)
    _process(raw_locked, locked=True)

    truncated = len(filtered) > limit
    return Response({
        'username': username,
        'directories': filtered[:limit],
        'returned_dirs': min(len(filtered), limit),
        'matched_dirs': len(filtered),
        'total_dirs': len(raw_dirs),
        'locked_dirs': len(raw_locked),
        'total_files': total_files,
        'truncated': truncated,
    })


@api_view(['DELETE'])
def delete_download(request, download_id):
    """Remove a single download row from our DB. If still active in slskd,
    cancel and remove it there too so the row really disappears."""
    try:
        dl = Download.objects.get(id=download_id)
    except Download.DoesNotExist:
        return Response({'error': 'Download not found'}, status=404)

    if dl.slskd_id and dl.status in ('queued', 'downloading'):
        client = SlskdClient()
        try:
            client.cancel_download(dl.username, dl.slskd_id, remove=True)
        except Exception as e:
            logger.warning(f"slskd cancel during delete failed: {e}")

    dl.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


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
