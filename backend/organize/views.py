import threading

import mimetypes
import os

from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework import status as http_status
from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db.models import Count
from django.http import FileResponse
from django.urls import reverse
from django.utils import timezone

from .models import PipelineItem
from .serializers import PipelineItemSerializer


_DOWNLOAD_URL_TTL_SECONDS = 120  # codex: short TTL shrinks leak window, mint-on-click keeps UX fine


def _download_signer() -> TimestampSigner:
    """TimestampSigner scoped to the download-url feature.

    salt namespaces the signature so a token minted for one purpose
    can't be replayed against another signed endpoint later.
    """
    return TimestampSigner(salt='organize.download-url.v1')


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
    from .services.pipeline import (
        process_all_pending,
        try_claim_processing_all,
        release_processing_all,
    )

    items = PipelineItem.objects.filter(stage='downloaded')
    count = items.count()
    if count == 0:
        return Response({'message': 'No items to process', 'count': 0})

    # Claim atomically so two concurrent requests can't both spawn a worker.
    if not try_claim_processing_all():
        return Response(
            {'error': 'Pipeline is already processing'},
            status=http_status.HTTP_409_CONFLICT,
        )

    def _run():
        try:
            process_all_pending(already_claimed=True)
        except Exception:
            # process_all_pending only releases on its own happy path; ensure we release on crash
            release_processing_all()
            raise

    threading.Thread(target=_run, daemon=True).start()
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
def retag_directory(request):
    """Walk a directory and re-write ID3/FLAC artist+title tags using the
    current clean rules.

    Use this for files you've already dragged out of the pipeline (e.g.
    into your Electronic library) — the DB has lost track of them but the
    tags still need cleaning so iTunes / Music.app displays what you want.

    Body: { "path": "/music/Electronic", "dry_run": false, "recursive": false }
    """
    import mutagen
    from .services.tagger import _clean_metadata, write_tags

    root = request.data.get('path') or '/music/Electronic'
    dry_run = bool(request.data.get('dry_run', False))
    recursive = bool(request.data.get('recursive', False))

    audio_exts = {'.mp3', '.flac', '.aiff', '.aif', '.wav', '.m4a', '.ogg'}
    if not os.path.isdir(root):
        return Response({'error': f'not a directory: {root}'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    cleaned = []
    skipped = 0
    errors = []

    walker = os.walk(root) if recursive else [(root, [], os.listdir(root))]
    for dirpath, _dirs, filenames in walker:
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in audio_exts:
                skipped += 1
                continue
            full = os.path.join(dirpath, fn)
            try:
                audio = mutagen.File(full, easy=True)
                if audio is None:
                    skipped += 1
                    continue
                old_artist = (audio.get('artist') or [''])[0]
                old_title = (audio.get('title') or [''])[0]
                if not old_artist and not old_title:
                    skipped += 1
                    continue
                cleaned_md = _clean_metadata({'artist': old_artist, 'title': old_title})
                new_artist = cleaned_md.get('artist', old_artist)
                new_title = cleaned_md.get('title', old_title)
                if new_artist == old_artist and new_title == old_title:
                    continue
                entry = {
                    'file': fn,
                    'artist': [old_artist, new_artist],
                    'title': [old_title, new_title],
                }
                cleaned.append(entry)
                if not dry_run:
                    # Use the low-level write (re-cleans again but that's idempotent).
                    meta = {'artist': new_artist, 'title': new_title}
                    write_tags(full, meta)
            except Exception as e:
                errors.append({'file': fn, 'error': str(e)})
        if not recursive:
            break

    return Response({
        'path': root,
        'recursive': recursive,
        'dry_run': dry_run,
        'changed': len(cleaned),
        'skipped': skipped,
        'errors': errors,
        'samples': cleaned[:20],
    })


def _find_file_by_basename(basename: str, search_roots: list[str],
                           max_entries: int = 50000) -> str | None:
    """Walk the given roots looking for a file with matching basename."""
    seen = 0
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, filenames in os.walk(root):
            for fn in filenames:
                seen += 1
                if seen > max_entries:
                    return None
                if fn == basename:
                    return os.path.join(dirpath, fn)
    return None


@api_view(['POST'])
def pipeline_retag_clean(request):
    """Re-clean artist/title in tags + DB for every item in the given stage.

    Uses the current renamer rules so ID3/FLAC tags match what the filenames
    now look like (iTunes reads tags, not filenames). Does NOT re-enrich from
    Discogs/MusicBrainz — purely a normalization pass on what's already stored.

    Self-heals stale paths: if the tracked file no longer exists at
    item.current_path (user moved it out of the pipeline), searches for the
    same basename under MUSIC_ROOT and retags wherever it actually lives.
    """
    from .services.tagger import write_tags
    from .services.renamer import clean_artist, clean_title, _strip_artist_prefix
    from core.services.config import get_config

    stage = request.data.get('stage', 'ready')
    items = PipelineItem.objects.filter(stage=stage)
    cleaned = 0
    relocated = 0
    errors = []

    search_roots = [get_config('MUSIC_ROOT')]
    for item in items:
        try:
            new_a = clean_artist(item.artist or '')
            new_t = clean_title(item.title or '')
            if new_a:
                new_t = _strip_artist_prefix(new_t, new_a)
            target_path = item.current_path
            if not os.path.exists(target_path):
                basename = item.final_filename or os.path.basename(item.current_path or '')
                if basename:
                    found = _find_file_by_basename(basename, search_roots)
                    if found:
                        target_path = found
                        relocated += 1
                        item.current_path = found
                        item.save(update_fields=['current_path'])
                    else:
                        errors.append({'id': item.id, 'error': f'file missing and not found under {search_roots}'})
                        continue
                else:
                    errors.append({'id': item.id, 'error': 'no basename to search for'})
                    continue
            metadata = {
                'artist': new_a, 'title': new_t,
                'album': item.album, 'label': item.label,
                'catalog_number': item.catalog_number,
                'genre': item.genre, 'year': item.year,
                'track_number': item.track_number,
            }
            write_tags(target_path, metadata)
            changed = (new_a != item.artist) or (new_t != item.title)
            if changed:
                item.artist = new_a
                item.title = new_t
                item.save(update_fields=['artist', 'title'])
                cleaned += 1
        except Exception as e:
            errors.append({'id': item.id, 'error': str(e)})
    return Response({
        'stage': stage,
        'total': items.count(),
        'cleaned': cleaned,
        'relocated': relocated,
        'errors': errors[:30],
    })


@api_view(['POST'])
def pipeline_rerename_all(request):
    """Re-run rename on every item currently in 'ready' (or the given stage).

    Useful after a rename-template change so existing filenames pick up the
    new convention without a full reprocess.
    """
    from .services.renamer import rename_file

    stage = request.data.get('stage', 'ready')
    items = PipelineItem.objects.filter(stage=stage)
    renamed = 0
    errors = []
    for item in items:
        try:
            before = item.current_path
            rename_file(item)
            if item.current_path != before:
                renamed += 1
        except Exception as e:
            errors.append({'id': item.id, 'error': str(e)})
    return Response({
        'stage': stage,
        'total': items.count(),
        'renamed': renamed,
        'errors': errors,
    })


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


# ─── Travel-mode downloads ────────────────────────────────────────────────
# Issue a short-lived HMAC-signed URL that streams the file without
# requiring a login. Enumerable /pipeline/<id>/download/ would be
# scrape-friendly; signed tokens keep this safe until CF Access is added.

@api_view(['POST'])
def pipeline_download_url(request, pk):
    """Issue a signed URL the caller can hand to the browser (or `curl -O`).

    Returns 410 Gone when bytes have already been drained to the home Mac —
    the only copy now lives in Music.app and can't be re-served.
    """
    try:
        item = PipelineItem.objects.get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'not found'}, status=http_status.HTTP_404_NOT_FOUND)

    if item.archive_state == 'archived':
        return Response(
            {
                'error': 'archived',
                'message': 'file is on your home Mac; download not available',
                'music_persistent_id': item.music_persistent_id,
            },
            status=http_status.HTTP_410_GONE,
        )

    path = item.work_path or item.current_path
    if not path or not os.path.exists(path):
        return Response(
            {'error': 'file missing on server'},
            status=http_status.HTTP_404_NOT_FOUND,
        )

    token = _download_signer().sign(str(item.id))
    signed_path = reverse('pipeline-download-signed', args=[token])
    expires_at = timezone.now() + timezone.timedelta(seconds=_DOWNLOAD_URL_TTL_SECONDS)

    resp = Response({
        'url': signed_path,
        'expires_at': expires_at.isoformat(),
        'ttl_seconds': _DOWNLOAD_URL_TTL_SECONDS,
        'filename': os.path.basename(path),
    })
    # Signed URLs are per-request bearer tokens — never cache.
    resp['Cache-Control'] = 'private, no-store, max-age=0'
    return resp


@api_view(['GET'])
def pipeline_download_signed(request, token):
    """Verify the signed token + stream the file. Safe against scraping.

    A race where drain archives the row between signing and streaming is
    caught here: we re-read DB state before opening the file. Open fd on
    an unlinked inode still streams to completion for in-flight downloads
    that started before the archive.
    """
    try:
        item_id = _download_signer().unsign(token, max_age=_DOWNLOAD_URL_TTL_SECONDS)
    except SignatureExpired:
        return Response(
            {'error': 'link expired; request a new one'},
            status=http_status.HTTP_410_GONE,
        )
    except BadSignature:
        return Response(
            {'error': 'invalid or tampered link'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    try:
        item = PipelineItem.objects.get(pk=int(item_id))
    except (ValueError, PipelineItem.DoesNotExist):
        return Response({'error': 'not found'}, status=http_status.HTTP_404_NOT_FOUND)

    if item.archive_state == 'archived':
        return Response(
            {
                'error': 'archived',
                'message': 'file is on your home Mac; download not available',
            },
            status=http_status.HTTP_410_GONE,
        )

    path = item.work_path or item.current_path
    if not path or not os.path.exists(path):
        return Response(
            {'error': 'file missing on server'},
            status=http_status.HTTP_404_NOT_FOUND,
        )

    filename = os.path.basename(path)
    content_type, _ = mimetypes.guess_type(filename)
    response = FileResponse(
        open(path, 'rb'),
        as_attachment=True,
        filename=filename,
        content_type=content_type or 'application/octet-stream',
    )
    # No caching, no leaky referrer if the tokenized URL ever becomes
    # the current document URL.
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@api_view(['POST'])
def pipeline_send_home(request, pk):
    """Manually publish a ready track so the drain daemon picks it up.

    Only needed when auto-publish is disabled. Idempotent: returns 200
    with existing state if the item is already past 'publishable'.
    """
    try:
        item = PipelineItem.objects.get(pk=pk)
    except PipelineItem.DoesNotExist:
        return Response({'error': 'not found'}, status=http_status.HTTP_404_NOT_FOUND)

    if item.archive_state in ('publishable', 'draining', 'archived'):
        return Response({
            'id': item.id,
            'archive_state': item.archive_state,
            'idempotent': True,
        })

    if item.stage != 'ready':
        return Response(
            {'error': f'cannot publish from stage={item.stage}'},
            status=http_status.HTTP_409_CONFLICT,
        )

    from .services.publisher import publish_pipeline_item
    try:
        published = publish_pipeline_item(item)
    except Exception as exc:
        return Response(
            {'error': f'publish failed: {exc}'},
            status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response({
        'id': published.id,
        'archive_state': published.archive_state,
        'work_path': published.work_path,
    })


# ─── Ad-hoc uploads — feed the pipeline tracks not from slskd ─────────────

_AUDIO_EXTS = {'.mp3', '.flac', '.wav', '.aiff', '.aif', '.m4a', '.ogg'}


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
def pipeline_upload(request):
    """Accept one or many audio files as multipart/form-data.

    Each file lands in 01_downloaded/ and becomes a PipelineItem at
    stage='downloaded'. The caller can optionally pass ?autoprocess=1 to
    kick off `process_all_pending` once all files land. Called from the
    Organize page drag-drop / picker AND from the Mac folder-watch daemon
    (which SSH-writes files then hits this endpoint with a manifest-only
    POST to trigger processing).
    """
    from .services.pipeline import (
        STAGE_FOLDERS, get_pipeline_root, ensure_pipeline_folders,
        process_all_pending, try_claim_processing_all, release_processing_all,
    )
    files = request.FILES.getlist('files') or (
        [request.FILES['file']] if 'file' in request.FILES else []
    )
    if not files:
        return Response(
            {'error': 'no files in multipart payload (expected "files" or "file")'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    ensure_pipeline_folders()
    dest_dir = os.path.join(get_pipeline_root(), STAGE_FOLDERS['downloaded'])

    created = []
    skipped = []
    for f in files:
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in _AUDIO_EXTS:
            skipped.append({'name': f.name, 'reason': f'unsupported extension {ext}'})
            continue

        # Collision handling: append _<n> until we find a free slot.
        basename = f.name
        dest_path = os.path.join(dest_dir, basename)
        counter = 1
        while os.path.exists(dest_path):
            stem, ext_ = os.path.splitext(f.name)
            dest_path = os.path.join(dest_dir, f'{stem}_{counter}{ext_}')
            counter += 1

        with open(dest_path, 'wb') as out:
            for chunk in f.chunks():
                out.write(chunk)

        item = PipelineItem.objects.create(
            original_filename=os.path.basename(dest_path),
            current_path=dest_path,
            stage='downloaded',
            archive_state='on_workbench',
            metadata_source='manual',
        )
        created.append({'id': item.id, 'filename': os.path.basename(dest_path)})

    autoprocess = request.query_params.get('autoprocess') == '1' or request.data.get('autoprocess') in (True, '1', 'true')
    kicked = False
    if autoprocess and created:
        if try_claim_processing_all():
            import threading
            def _run():
                try:
                    process_all_pending(already_claimed=True)
                except Exception:
                    release_processing_all()
                    raise
            threading.Thread(target=_run, daemon=True).start()
            kicked = True

    return Response({
        'created': created,
        'skipped': skipped,
        'pipeline_kicked': kicked,
    }, status=http_status.HTTP_201_CREATED if created else http_status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def pipeline_kick(request):
    """Trigger process_all_pending without uploading anything.

    Used by the Mac folder-watch daemon: it writes files directly to VPS
    `/srv/ocdj/pipeline/01_downloaded/` via SSH+rsync (no HTTP upload to
    avoid re-encoding multipart for multi-GB days), then hits this to
    start processing. Also hit by the Upload-UI after a scan run.

    Returns 409 if a pipeline run is already in progress.
    """
    from .services.pipeline import (
        scan_completed_downloads,
        process_all_pending, try_claim_processing_all, release_processing_all,
    )
    created = scan_completed_downloads()
    items = PipelineItem.objects.filter(stage='downloaded')
    count = items.count()
    if count == 0:
        return Response({'message': 'nothing pending', 'scanned_created': created, 'count': 0})
    if not try_claim_processing_all():
        return Response(
            {'error': 'pipeline already running', 'scanned_created': created},
            status=http_status.HTTP_409_CONFLICT,
        )
    import threading
    def _run():
        try:
            process_all_pending(already_claimed=True)
        except Exception:
            release_processing_all()
            raise
    threading.Thread(target=_run, daemon=True).start()
    return Response({
        'message': f'kicked processing for {count} items',
        'scanned_created': created,
        'count': count,
    })
