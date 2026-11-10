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

from organize.models import PipelineItem
from organize.serializers import PipelineItemSerializer
from wanted.models import WantedItem
from .models import SearchQueueItem, SearchResult, Download, QualityPreset
from .serializers import (
    SearchQueueItemSerializer, AddToQueueSerializer,
    SearchResultSerializer, DownloadSerializer, QualityPresetSerializer,
    SearchRequestSerializer, DownloadRequestSerializer,
)
from .services import SlskdClient, generate_queries, score_result, filter_results, extract_file_info, simplify_query, normalize_text

from .lifecycle import (
    ACTIVE, hide_downloads, is_terminal_transfer as _is_terminal_transfer,
    match_downloads, reconcile_downloads, sync_linked_items, transfer_entries, transfer_status, update_transfer,
)

logger = logging.getLogger(__name__)


# ── Background search worker ────────────────────────────────

def _run_search_for_queue_item(queue_item_id, started=None):
    """Run slskd search in background thread. Updates SearchQueueItem status when done."""
    try:
        qi = SearchQueueItem.objects.filter(pk=queue_item_id, status='searching').first()
        if qi is None or (started is not None and qi.updated != started):
            return
        attempt = SearchQueueItem.objects.filter(pk=queue_item_id, status='searching', updated=qi.updated)

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
            _fail_search(attempt, 'No valid search query could be generated')
            return

        client = SlskdClient()
        all_results = []
        completed_queries = 0

        for query in queries[:2]:
            if not attempt.exists():
                return
            search_id = None
            try:
                search_response = client.search(query)
                search_id = search_response.get('id')
                if not search_id:
                    continue

                # Poll for completion — slskd searchTimeout is 15s, so 30s max
                for _ in range(6):
                    time.sleep(5)
                    if not attempt.exists():
                        return
                    search_data = client.get_search(search_id, include_responses=False)
                    if not search_data:
                        break
                    state = search_data.get('state', '')
                    if 'Completed' in state:
                        break

                # Extract results — `files` are freely available, `lockedFiles`
                # are gated to privileged peers. Keep both so the UI can show
                # the 🔒 indicator instead of hiding them silently.
                if not attempt.exists():
                    return
                search_data = client.get_search(search_id) or {}
                completed_queries += 1
                for response in search_data.get('responses', []):
                    username = response.get('username', '')
                    upload_speed = response.get('uploadSpeed', 0)
                    free_slots = response.get('hasFreeUploadSlot', False)
                    queue_length = response.get('queueLength', 0)
                    for key in ('files', 'lockedFiles'):
                        for file_data in response.get(key) or []:
                            all_results.append({
                                **file_data, '_username': username, '_upload_speed': upload_speed,
                                '_free_slots': free_slots, '_queue_length': queue_length,
                                '_is_locked': key == 'lockedFiles' or file_data.get('isLocked') is True,
                            })

                if len(all_results) > 10:
                    break

            except Exception:
                logger.warning('Soulseek search request failed for queue item %s', queue_item_id)
            finally:
                if search_id:
                    try:
                        client.delete_search(search_id)
                    except Exception:
                        pass

        if not completed_queries:
            _fail_search(attempt, 'Soulseek did not complete the search. Try again.')
            return

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

        with transaction.atomic():
            qi = attempt.select_for_update().first()
            if qi is None:
                return
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
            qi.search_count += 1
            qi.last_searched = timezone.now()
            if scored:
                qi.best_match_score = scored[0]['match_score']
                qi.status = 'found'
            else:
                qi.best_match_score = 0
                qi.status = 'not_found'
            qi.save()

            if qi.wanted_item_id:
                WantedItem.objects.filter(pk=qi.wanted_item_id, status='searching').update(
                    search_count=qi.search_count, last_searched=qi.last_searched,
                    best_match_score=qi.best_match_score, status=qi.status,
                )

        logger.info("Soulseek search %s complete: %s results", queue_item_id, len(scored))

    except Exception:
        logger.exception("Background search failed for queue item %s", queue_item_id)
        if 'attempt' in locals():
            _fail_search(attempt, 'Search failed. Try again.')
    finally:
        db.connections.close_all()


def _fail_search(attempt, message):
    with transaction.atomic():
        qi = attempt.select_for_update().first()
        if qi is None:
            return
        qi.status = 'failed'
        qi.error_message = message
        qi.save(update_fields=['status', 'error_message', 'updated'])
        if qi.wanted_item_id:
            WantedItem.objects.filter(pk=qi.wanted_item_id, status='searching').update(
                status='failed', error_message=message,
            )


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

    @action(detail=True, methods=['post'], url_path='cancel-search')
    def cancel_search(self, request, pk=None):
        with transaction.atomic():
            qi = SearchQueueItem.objects.select_for_update().filter(pk=pk).first()
            if qi is None:
                return Response({'error': 'Queue item not found'}, status=404)
            if qi.status == 'searching':
                qi.status = 'found' if qi.search_results.exists() else 'pending'
                qi.error_message = ''
                qi.save(update_fields=['status', 'error_message', 'updated'])
                if qi.wanted_item_id:
                    WantedItem.objects.filter(pk=qi.wanted_item_id, status='searching').update(
                        status=qi.status, error_message='',
                    )
        return Response({'status': qi.status})

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
def slskd_connect(request):
    """Tell slskd to log in to the Soulseek P2P network."""
    client = SlskdClient()
    ok, msg = client.connect_soulseek()
    if ok:
        return Response({'ok': True, 'message': msg})
    return Response({'ok': False, 'error': msg}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(['POST'])
def slskd_disconnect(request):
    client = SlskdClient()
    ok, msg = client.disconnect_soulseek()
    if ok:
        return Response({'ok': True, 'message': msg})
    return Response({'ok': False, 'error': msg}, status=status.HTTP_502_BAD_GATEWAY)


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

    with transaction.atomic():
        qi = SearchQueueItem.objects.select_for_update().filter(pk=qi.id).first()
        if qi is None:
            return Response({'error': 'Queue item not found'}, status=404)
        if qi.status == 'searching':
            return Response({'status': 'already_searching', 'queue_item_id': qi.id})
        qi.status = 'searching'
        qi.error_message = ''
        qi.save(update_fields=['status', 'error_message', 'updated'])
        if qi.wanted_item_id:
            WantedItem.objects.filter(pk=qi.wanted_item_id).update(status='searching', error_message='')

    # Fire background thread after commit so it sees the 'searching' row
    def _start_search():
        threading.Thread(
            target=_run_search_for_queue_item,
            args=(qi.id, qi.updated),
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
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 502
        logger.warning('slskd rejected download enqueue (HTTP %s)', code)
        return Response({'error': 'Soulseek could not queue this file. Refresh the results and try again.'},
                        status=code if 400 <= code < 500 else 502)
    except requests.RequestException:
        return Response({'error': 'Soulseek did not confirm the request. Check Downloads before retrying.'},
                        status=502)

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

    with transaction.atomic():
        # Serialize local enqueue with linked-state reconciliation. Queue-only
        # requests inherit their Wanted provenance just like auto-downloads.
        qi = SearchQueueItem.objects.select_for_update().filter(pk=queue_item_id).first()
        wanted_item_id = wanted_item_id or (qi.wanted_item_id if qi else None)
        wi = WantedItem.objects.select_for_update().filter(pk=wanted_item_id).first()
        download = Download.objects.create(
            username=username, filename=filename, queue_item_id=queue_item_id,
            wanted_item_id=wanted_item_id, slskd_id=slskd_id, status='queued',
        )
        if qi:
            SearchQueueItem.objects.filter(pk=qi.pk).update(status='downloading', updated=timezone.now())
        if wi:
            WantedItem.objects.filter(pk=wi.pk).update(status='downloading', updated=timezone.now())

    logger.info(f"Download queued: {filename} from {username} (slskd_id={slskd_id})")
    return Response(DownloadSerializer(download).data, status=201)


def _refresh_downloads(entries):
    """Capture outcomes before history removal or the display cap can hide them."""
    from organize.tasks import ingest_soulseek_download

    return reconcile_downloads(entries, on_completed=ingest_soulseek_download)


@api_view(['GET'])
def downloads_status(request):
    """
    Get download statuses merged with slskd transfer progress.
    Returns enriched download objects with percent, speed, state from slskd.
    Also returns a set of downloading/downloaded filenames for UI indicators.
    """
    client = SlskdClient()

    slskd_unreachable = False
    slskd_error = ''
    entries = []
    try:
        entries = transfer_entries(client.get_downloads())
    except Exception as e:
        slskd_unreachable = True
        slskd_error = str(e)[:200]
        logger.warning(f"Could not fetch slskd downloads: {e}")

    all_downloads, matches = _refresh_downloads(entries)
    if not slskd_unreachable:
        sync_linked_items(entries)

    # Claim matches using ALL rows, including hidden history and rows beyond 50,
    # so neither can come back as an apparently untracked transfer.
    matched = set(matches.values())
    history = [dl for dl in all_downloads if dl.hidden_at is None and dl.status not in ACTIVE]
    history_ids = {dl.pk for dl in history[:50]}
    our_downloads = [dl for dl in all_downloads if dl.status in ACTIVE or dl.pk in history_ids]
    enriched = []
    for dl in our_downloads:
        data = DownloadSerializer(dl).data
        transfer = entries[matches[dl.pk]][1] if dl.pk in matches else {}
        data.update({
            'slskd_state': transfer.get('state', ''),
            'percent': transfer.get('percentComplete', dl.progress),
            'speed': transfer.get('averageSpeed', 0),
            'bytes_transferred': transfer.get('bytesTransferred', 0),
            'bytes_remaining': transfer.get('bytesRemaining', 0),
            'elapsed': transfer.get('elapsedTime', ''),
            'remaining': transfer.get('remainingTime', ''),
            'slskd_transfer_id': transfer.get('id', ''),
        })
        enriched.append(data)

    for index, (username, transfer) in enumerate(entries):
        if index in matched:
            continue
        state = transfer.get('state', '') or ''
        if 'Cancelled' in state:
            continue
        status = transfer_status(transfer)
        enriched.append({
            'id': f"slskd:{username.casefold()}:{transfer.get('id', '')}",
            'untracked': True,
            'username': username,
            'filename': transfer.get('filename', ''),
            'status': status,
            'slskd_state': state,
            'progress': transfer.get('percentComplete', 0),
            'percent': transfer.get('percentComplete', 0),
            'speed': transfer.get('averageSpeed', 0),
            'bytes_transferred': transfer.get('bytesTransferred', 0),
            'bytes_remaining': transfer.get('bytesRemaining', 0),
            'elapsed': transfer.get('elapsedTime', ''),
            'remaining': transfer.get('remainingTime', ''),
            'slskd_transfer_id': transfer.get('id', ''),
            'error_message': state if status == 'failed' else '',
            'started': transfer.get('requestedAt', ''),
        })

    # These are acquisition indicators, not a history listing. Hidden successes
    # still count, but a live retry wins regardless of iteration/creation order.
    download_indicators = {}
    priority = {'completed': 1, 'queued': 2, 'downloading': 3}
    indicators = [(dl.filename, dl.status) for dl in all_downloads]
    indicators.extend((t.get('filename') or '', transfer_status(t)) for _, t in entries)
    for filename, status in indicators:
        key = filename.lower()
        if priority.get(status, 0) > priority.get(download_indicators.get(key), 0):
            download_indicators[key] = status

    completed = {row['id']: row for row in enriched if row['status'] == 'completed' and not row.get('untracked')}
    serializer = PipelineItemSerializer()
    for item in PipelineItem.objects.filter(download_id__in=completed).order_by('id'):
        row = completed[item.download_id]
        if 'artifact' in row:
            continue
        row['artifact'] = {
            'id': item.id,
            'artist': item.artist,
            'title': item.title,
            'archive_state': item.archive_state,
            'archive_library_match': serializer.get_archive_library_match(item),
            'playback_url': serializer.get_playback_url(item),
            'playback_unavailable': serializer.get_playback_unavailable(item),
        }

    return Response({
        'downloads': enriched,
        'download_indicators': download_indicators,
        'slskd_unreachable': slskd_unreachable,
        'slskd_error': slskd_error,
    })


def _peer_transfers(users, username):
    return [
        (peer['username'], transfer)
        for peer in users if peer.get('username', '').casefold() == username.casefold()
        for directory in peer.get('directories', [])
        for transfer in directory.get('files', [])
    ]


@api_view(['POST'])
def cancel_download(request):
    """Cancel an active transfer without removing an unrelated retry."""
    dl_id = request.data.get('download_id')
    client = SlskdClient()
    if isinstance(dl_id, str) and dl_id.startswith('slskd:'):
        username, _, transfer_id = dl_id[len('slskd:'):].rpartition(':')
        if not username or not transfer_id:
            return Response({'error': 'Invalid slskd transfer ID'}, status=400)
        try:
            if any(t.get('id') == transfer_id for _, t in _peer_transfers(client.get_downloads(), username)):
                client.cancel_download(username, transfer_id, remove=True)
        except requests.RequestException:
            return Response({'error': 'Could not cancel the transfer. Refresh and try again.'}, status=502)
        return Response({'status': 'cancelled', 'id': dl_id, 'untracked': True})

    if not str(dl_id).isdecimal():
        return Response({'error': 'Invalid download ID'}, status=400)
    dl = Download.objects.filter(pk=dl_id).first()
    if dl is None:
        return Response({'error': 'Download not found'}, status=404)
    if dl.status not in ('queued', 'downloading'):
        return Response({'error': 'This transfer already ended. Refresh Downloads.'}, status=409)
    try:
        entries = transfer_entries(client.get_downloads())
        matches = [(peer, t) for peer, t in entries
                   if peer.casefold() == dl.username.casefold()
                   and (t.get('filename') or '').casefold() == dl.filename.casefold()]
        active = [(peer, t) for peer, t in matches if not _is_terminal_transfer(t.get('state'))]
        if active and not dl.slskd_id and dl.pk not in match_downloads(list(Download.objects.all()), entries):
            return Response({'error': 'The active transfer is ambiguous. Refresh Downloads.'}, status=409)
        if len(active) > 1 or any(not t.get('id') or (dl.slskd_id and t['id'] != dl.slskd_id) for _, t in active):
            return Response({'error': 'The active transfer changed. Refresh Downloads.'}, status=409)
        if active:
            peer, transfer = active[0]
            client.cancel_download(peer, transfer['id'], remove=True)
        elif matches:
            return Response({'error': 'This transfer already ended. Refresh Downloads.'}, status=409)
    except requests.RequestException:
        return Response({'error': 'Could not cancel the transfer. Refresh and try again.'}, status=502)

    Download.objects.filter(pk=dl.pk, status__in=ACTIVE).update(status='cancelled')
    # The successful cancellation removed that live attempt from the snapshot.
    remaining = [(peer, t) for peer, t in entries
                 if not any(peer == p and t == transfer for p, transfer in active)]
    sync_linked_items(remaining)

    return Response({'status': 'cancelled', 'id': dl.id})


AUDIO_EXT_SET = {'mp3', 'flac', 'wav', 'aiff', 'aif', 'ogg', 'opus', 'wma', 'aac', 'm4a'}


def _normalized_peer_path(path):
    return (path or '').replace('\\', '/').casefold().strip('/')


def _files_from_search(username, directory, queue_item_id):
    if not queue_item_id:
        return []
    try:
        queue_item_id = int(queue_item_id)
    except ValueError:
        return []
    parent = _normalized_peer_path(directory)
    files = {}
    for result in SearchResult.objects.filter(username=username, queue_item_id=queue_item_id):
        if _normalized_peer_path(result.filename).rpartition('/')[0] != parent:
            continue
        if result.filename in files and result.is_locked:
            continue
        files[result.filename] = {
            'filename': result.filename, 'size': result.file_size, 'extension': result.file_extension,
            'bitRate': result.bitrate, 'sampleRate': result.sample_rate, 'bitDepth': result.bit_depth,
            'isLocked': result.is_locked,
        }
    return list(files.values())


@api_view(['GET'])
def browse_user(request):
    """Browse a Soulseek user's shared files.

    Query params:
      username: required
      directory: exact peer path from a search result, passed through verbatim.
      queue_item_id: search results to use if the peer does not list that folder.
      dir_prefix: optional text to filter the peer's full share by folder path.
      audio_only: '1' to drop non-audio files (default: include everything).
      limit: max number of directories to return (default 200).
    """
    username = request.query_params.get('username', '').strip()
    if not username:
        return Response({'error': 'username is required'}, status=400)

    directory = request.query_params.get('directory', '')
    if len(directory) > 1000:
        return Response({'error': 'Folder path is too long'}, status=400)
    dir_prefix = request.query_params.get('dir_prefix', '').strip()
    audio_only = request.query_params.get('audio_only') in ('1', 'true', 'True')
    try:
        limit = int(request.query_params.get('limit', 200))
    except (TypeError, ValueError):
        limit = 200

    client = SlskdClient()
    partial = False
    try:
        if directory:
            lookup_error = None
            try:
                listing = client.directory_contents(username, directory)
            except (requests.exceptions.HTTPError, requests.exceptions.Timeout) as exc:
                listing = []
                lookup_error = exc
            if not any(d.get('files') for d in listing):
                files = _files_from_search(username, directory, request.query_params.get('queue_item_id'))
                if files:
                    listing = [{'name': directory, 'files': files}]
                    partial = True
                elif lookup_error:
                    raise lookup_error
            data = {'directories': listing}
        else:
            data = client.browse_user(username)
    except requests.exceptions.HTTPError as e:
        body = ''
        try:
            body = e.response.text if e.response is not None else ''
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
        return Response({'error': msg}, status=e.response.status_code if e.response is not None else 502)
    except requests.exceptions.Timeout:
        return Response(
            {'error': f'Browse timed out — {username} may be offline.'},
            status=504,
        )
    except Exception as e:
        return Response({'error': f'Browse failed: {e}'}, status=502)

    raw_dirs = data.get('directories', []) if isinstance(data, dict) else []
    raw_locked = data.get('lockedDirectories', []) if isinstance(data, dict) else []

    def _download_path(directory, filename):
        # Browse reports basenames, whereas slskd transfers require the full peer path.
        if not directory or not filename or '\\' in filename or '/' in filename:
            return filename
        if directory.endswith(('\\', '/')):
            return directory + filename
        separator = '/' if '/' in directory and '\\' not in directory else '\\'
        return directory + separator + filename

    path_filter = _normalized_peer_path(dir_prefix)
    word_filter = normalize_text(dir_prefix)
    filtered = []
    total_files = 0

    def _process(dir_list, locked):
        nonlocal total_files
        for d in dir_list:
            name = d.get('name', '')
            if (path_filter and path_filter not in _normalized_peer_path(name)
                    and not (word_filter and word_filter in normalize_text(name))):
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
                        'filename': _download_path(name, f.get('filename', '')),
                        'size': f.get('size', 0),
                        'extension': (f.get('extension') or '').lstrip('.'),
                        'bitrate': f.get('bitRate'),
                        'sample_rate': f.get('sampleRate'),
                        'bit_depth': f.get('bitDepth'),
                        'length': f.get('length'),
                        'locked': locked or f.get('isLocked') is True,
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
        'partial': partial,
    })


@api_view(['DELETE'])
def delete_download(request, download_id=None):
    """Dismiss one terminal history entry while retaining its provenance."""
    dl = None
    if download_id is None:
        value = request.data.get('download_id')
        if not isinstance(value, str) or not value.startswith('slskd:'):
            return Response({'error': 'Invalid slskd transfer ID'}, status=400)
        username, _, transfer_id = value[len('slskd:'):].rpartition(':')
        if not username or not transfer_id:
            return Response({'error': 'Invalid slskd transfer ID'}, status=400)
    else:
        dl = Download.objects.filter(pk=download_id).first()
        if dl is None:
            return Response({'error': 'Download not found'}, status=404)
        if dl.status not in ('completed', 'failed', 'cancelled'):
            return Response({'error': 'This transfer is still active. Cancel it first.'}, status=409)
        if dl.hidden_at is not None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        username, transfer_id = dl.username, dl.slskd_id

    client = SlskdClient()
    try:
        entries = transfer_entries(client.get_downloads())
        transfers = [(peer, t) for peer, t in entries if peer.casefold() == username.casefold()]
        same_file = [(peer, t) for peer, t in transfers if dl is not None
                     and (t.get('filename') or '').casefold() == dl.filename.casefold()]
        if dl is not None and same_file and not dl.slskd_id and dl.pk not in match_downloads(list(Download.objects.all()), entries):
            return Response({'error': 'A matching transfer is ambiguous. Refresh Downloads.'}, status=409)
        if any(not _is_terminal_transfer(t.get('state')) for _, t in same_file):
            return Response({'error': 'A retry of this download is still active.'}, status=409)
        matches = ([(peer, t) for peer, t in transfers if t.get('id') == transfer_id]
                   if transfer_id else same_file)
        if len(matches) > 1 or any(not _is_terminal_transfer(t.get('state')) for _, t in matches):
            return Response({'error': 'A matching transfer is active or ambiguous. Refresh and try again.'}, status=409)
        if matches:
            peer, transfer = matches[0]
            if not transfer.get('id'):
                return Response({'error': 'The slskd transfer has no ID'}, status=502)
            client.remove_download_history(peer, transfer['id'])
    except Exception:
        return Response({'error': 'Could not remove the transfer from slskd. Refresh and try again.'}, status=502)

    if dl is not None:
        if matches:
            update_transfer(dl, matches[0][1])
        hide_downloads([dl.pk])
    remaining = [(peer, t) for peer, t in entries
                 if not any(peer == p and t == transfer for p, transfer in matches)]
    sync_linked_items(remaining)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
def clear_downloads(request):
    """Clear selected terminal slskd history, then hide local history once."""
    mode = request.data.get('mode', 'completed')
    statuses = {
        'completed': ['completed'],
        'failed': ['failed', 'cancelled'],
        'all': ['completed', 'failed', 'cancelled'],
    }
    if not isinstance(mode, str) or mode not in statuses:
        return Response({'error': 'Invalid mode'}, status=400)

    client = SlskdClient()
    try:
        users = client.get_downloads()
    except Exception:
        # Without live state we cannot safely distinguish stale local terminal
        # rows from retried/active transfers, or prevent slskd rows reappearing.
        return Response({'error': 'Could not reach slskd. History was preserved; try again.',
                         'cleared': 0, 'slskd_cleared': 0}, status=502)

    # Persist known outcomes before deleting slskd's only remaining evidence.
    _refresh_downloads(transfer_entries(users))
    transfers_by_id = {}
    transfers_by_file = {}
    removed = set()
    failed = 0
    for user in users:
        username = user.get('username', '')
        for directory in user.get('directories', []):
            for transfer in directory.get('files', []):
                tid = transfer.get('id', '')
                key = (username.lower(), tid)
                transfers_by_id[key] = transfer
                file_key = (username.lower(), transfer.get('filename', '').lower())
                transfers_by_file.setdefault(file_key, []).append(key)
                state = {part.strip() for part in (transfer.get('state') or '').split(',')}
                if not _is_terminal_transfer(transfer.get('state')):
                    continue
                succeeded = 'Succeeded' in state
                if (mode == 'completed' and not succeeded) or (mode == 'failed' and succeeded):
                    continue
                if not tid:
                    failed += 1
                    continue
                try:
                    client.remove_download_history(username, tid)
                    removed.add(key)
                except Exception:
                    # Keep failures visible and retryable; do not expose peer
                    # names, paths, or upstream response bodies to the browser.
                    failed += 1

    local_ids = []
    for dl in Download.objects.filter(status__in=statuses[mode], hidden_at__isnull=True):
        if dl.slskd_id:
            key = (dl.username.lower(), dl.slskd_id)
            keys = [key] if key in transfers_by_id else []
        else:
            # Legacy rows have no transfer id. Preserve them if any matching
            # transfer remains (including active retries of the same file).
            keys = transfers_by_file.get((dl.username.lower(), dl.filename.lower()), [])
        if all(key in removed for key in keys):
            local_ids.append(dl.pk)
    count = hide_downloads(local_ids)
    sync_linked_items([(peer, t) for peer, t in transfer_entries(users)
                       if (peer.lower(), t.get('id', '')) not in removed])
    result = {'cleared': count, 'slskd_cleared': len(removed), 'slskd_failed': failed}
    if failed:
        result['error'] = 'Some slskd history could not be cleared. Remaining entries were preserved; try again.'
    return Response(result, status=502 if failed else 200)


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
