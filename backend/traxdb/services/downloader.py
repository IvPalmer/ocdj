"""
Native Pixeldrain downloader for TraxDB.

Replicated from tools/traxdb_sync/download_from_report.py.
Downloads files from Pixeldrain lists, stores progress in DB models.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from django import db

from .pixeldrain import PixeldrainClient, is_pixeldrain_not_found

logger = logging.getLogger(__name__)


def _pick_dest_dir(traxdb_root: str, inferred_date: Optional[str], list_id: str) -> str:
    if inferred_date:
        return os.path.join(traxdb_root, inferred_date)
    return os.path.join(traxdb_root, "_inbox", list_id)


def _mark_list_ids_seen(traxdb_root: str, list_ids: List[str]) -> None:
    seen_path = os.path.join(traxdb_root, ".pixeldrain_lists_seen.json")
    existing: Set[str] = set()
    try:
        with open(seen_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                existing = {str(x) for x in data}
    except Exception:
        existing = set()

    merged = sorted(existing | {str(x) for x in list_ids})
    tmp_path = seen_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(merged, indent=2, ensure_ascii=False, fp=f)
    os.replace(tmp_path, seen_path)


def _write_progress(progress_path: str, data: Dict[str, Any]) -> None:
    tmp = progress_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, progress_path)


def run_download(operation_id: int, sync_report_path: Optional[str] = None, links_key: str = 'links_new'):
    """
    Run Pixeldrain download in a background thread.

    If sync_report_path is provided, reads links from the JSON report (legacy mode).
    Otherwise, reads ScrapedFolder records from the DB that need downloading.
    """
    from ..models import TraxDBOperation, ScrapedFolder, ScrapedTrack

    try:
        op = TraxDBOperation.objects.get(id=operation_id)
        op.status = 'running'
        op.save()

        traxdb_root = os.environ.get('TRAXDB_ROOT', '/music/Electronic/ID3/traxdb')
        pixeldrain_key = os.environ.get('PIXELDRAIN_API_KEY', '')

        if not pixeldrain_key:
            op.status = 'failed'
            op.error_message = 'PIXELDRAIN_API_KEY not configured'
            op.save()
            return

        client = PixeldrainClient(api_key=pixeldrain_key)

        # Determine which folders to download.
        # The DB (ScrapedFolder.download_status='pending') is the source of
        # truth — it accumulates across multiple sync runs. The latest sync's
        # `links_new` only reports lists *new in that one run* and goes empty
        # the moment you re-run sync, even when 17 lists are still pending.
        # Only fall back to the legacy report if explicitly provided.
        if sync_report_path and os.path.exists(sync_report_path):
            with open(sync_report_path, 'r', encoding='utf-8') as f:
                sr = json.load(f)
            links = sr.get(links_key) or []
        else:
            pending_folders = ScrapedFolder.objects.filter(download_status='pending')
            links = [
                {
                    'list_id': f.folder_id,
                    'pixeldrain_url': f.pixeldrain_url,
                    'inferred_date': f.inferred_date,
                    'source_url': f.url,
                }
                for f in pending_folders
            ]

        if not links:
            op.status = 'completed'
            op.summary = {'lists_total': 0, 'lists_completed': 0, 'files_total': 0, 'files_completed': 0}
            op.save()
            return

        # Set up progress tracking
        reports_dir = os.path.join(traxdb_root, '_reports')
        os.makedirs(reports_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        progress_path = os.path.join(reports_dir, f'download_{timestamp}_progress.json')
        op.progress_path = progress_path
        op.save()

        # Pre-scan to get totals and cache files
        total_lists = 0
        total_files = 0
        total_bytes = 0
        files_cache: Dict[str, list] = {}
        signature_seen: Dict[tuple, str] = {}

        for l in links:
            list_id = str(l.get('list_id') or '').strip()
            if not list_id:
                continue
            total_lists += 1
            try:
                files = list(client.iter_list_files(list_id))
                files_cache[list_id] = files
                sig = tuple(sorted((pf.name, pf.size) for pf in files))
                if sig in signature_seen:
                    total_lists -= 1
                    continue
                signature_seen[sig] = list_id
                total_files += len(files)
                for pf in files:
                    if isinstance(pf.size, int):
                        total_bytes += pf.size
            except Exception:
                pass

        progress = {
            'status': 'running',
            'lists_total': total_lists,
            'lists_completed': 0,
            'lists_dead': 0,
            'files_total': total_files,
            'files_completed': 0,
            'bytes_total': total_bytes,
            'bytes_downloaded': 0,
            'current_list': None,
        }
        _write_progress(progress_path, progress)

        out_lists = []
        dead_links = []
        errors = []
        downloaded_signatures: Dict[tuple, str] = {}
        last_progress_time = 0.0

        for l in links:
            list_id = str(l.get('list_id') or '').strip()
            if not list_id:
                continue

            inferred_date = l.get('inferred_date')
            dest_dir = _pick_dest_dir(traxdb_root, inferred_date, list_id)

            # Update folder status in DB
            try:
                folder = ScrapedFolder.objects.filter(folder_id=list_id).first()
                if folder:
                    folder.download_status = 'downloading'
                    folder.save(update_fields=['download_status'])
            except Exception:
                pass

            try:
                files = files_cache.get(list_id) or list(client.iter_list_files(list_id))

                # Dedupe identical lists
                sig = tuple(sorted((pf.name, pf.size) for pf in files))
                if sig in downloaded_signatures:
                    canonical = downloaded_signatures[sig]
                    out_lists.append({
                        'list_id': list_id,
                        'pixeldrain_url': l.get('pixeldrain_url'),
                        'inferred_date': inferred_date,
                        'dest_dir': dest_dir,
                        'skipped': True,
                        'skip_reason': 'identical_to_other_list',
                        'identical_to': canonical,
                        'files': [],
                    })
                    _mark_list_ids_seen(traxdb_root, [list_id])
                    if folder:
                        folder.download_status = 'downloaded'
                        folder.save(update_fields=['download_status'])
                    continue
                downloaded_signatures[sig] = list_id

                os.makedirs(dest_dir, exist_ok=True)

                progress['current_list'] = list_id
                _write_progress(progress_path, progress)

                item = {
                    'list_id': list_id,
                    'pixeldrain_url': l.get('pixeldrain_url'),
                    'inferred_date': inferred_date,
                    'dest_dir': dest_dir,
                    'files': [],
                }

                backup_root = os.path.join(traxdb_root, '_mismatch_backups', datetime.now().strftime('%Y%m%d-%H%M%S'))
                backup_created = False

                for pf in files:
                    dest_path = os.path.join(dest_dir, os.path.basename(pf.name))
                    status = 'downloaded'

                    # Handle size mismatch with backup
                    if os.path.exists(dest_path) and pf.size is not None:
                        existing_size = os.path.getsize(dest_path)
                        if existing_size != pf.size:
                            if not backup_created:
                                os.makedirs(backup_root, exist_ok=True)
                                backup_created = True
                            backup_path = os.path.join(backup_root, f"{list_id}__{os.path.basename(pf.name)}")
                            shutil.copy2(dest_path, backup_path)
                            os.remove(dest_path)
                            status = 'mismatch_backed_up'

                    downloaded, bytes_written = client.download_file(
                        pf.id,
                        dest_path,
                        expected_size=pf.size,
                        overwrite=False,
                        resume=True,
                    )

                    progress['files_completed'] += 1
                    progress['bytes_downloaded'] += int(bytes_written)

                    now = time.time()
                    if now - last_progress_time >= 5:
                        _write_progress(progress_path, progress)
                        last_progress_time = now

                    item['files'].append({
                        'name': pf.name,
                        'file_id': pf.id,
                        'expected_size': pf.size,
                        'dest_path': dest_path,
                        'downloaded': downloaded,
                        'bytes_written': bytes_written,
                        'status': status,
                    })

                    # Update track in DB
                    try:
                        if folder:
                            track = ScrapedTrack.objects.filter(
                                folder=folder, pixeldrain_file_id=pf.id
                            ).first()
                            if track:
                                track.downloaded = True
                                track.download_status = 'downloaded'
                                track.local_path = dest_path
                                track.save(update_fields=['downloaded', 'download_status', 'local_path'])
                    except Exception:
                        pass

                out_lists.append(item)
                _mark_list_ids_seen(traxdb_root, [list_id])
                progress['lists_completed'] += 1
                _write_progress(progress_path, progress)

                # Update folder status
                if folder:
                    folder.download_status = 'downloaded'
                    folder.save(update_fields=['download_status'])

            except Exception as e:
                if is_pixeldrain_not_found(e):
                    dead_links.append({
                        'list_id': list_id,
                        'pixeldrain_url': l.get('pixeldrain_url'),
                        'inferred_date': inferred_date,
                        'error': repr(e),
                    })
                    progress['lists_dead'] += 1
                else:
                    errors.append({'list_id': list_id, 'error': repr(e)})

                # Only persist 'failed' for genuinely dead lists (404). Other
                # errors (network, auth, rate-limit) are transient — leave the
                # folder as 'pending' so the next "Download New" retries it
                # without the user having to manually reset state.
                if folder:
                    if is_pixeldrain_not_found(e):
                        folder.download_status = 'failed'
                    else:
                        folder.download_status = 'pending'
                    folder.save(update_fields=['download_status'])

                _write_progress(progress_path, progress)

        # Final summary
        summary = {
            'lists_total': total_lists,
            'lists_completed': progress['lists_completed'],
            'lists_dead': progress.get('lists_dead', 0),
            'files_total': total_files,
            'files_completed': progress['files_completed'],
            'bytes_downloaded': progress['bytes_downloaded'],
            'errors_count': len(errors),
            'dead_links_count': len(dead_links),
            'lists': out_lists,
            'dead_links': dead_links,
            'errors': errors,
        }

        op.summary = summary
        op.status = 'completed' if not errors else 'failed'
        if errors:
            op.error_message = f'{len(errors)} errors during download'
        op.save()
        logger.info(f'Download #{operation_id} finished: {op.status}')

    except Exception as e:
        logger.exception(f'Download #{operation_id} crashed')
        try:
            op = TraxDBOperation.objects.get(id=operation_id)
            op.status = 'failed'
            op.error_message = str(e)
            op.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()
