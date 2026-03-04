"""
Native file audit for TraxDB.

Replicated from tools/traxdb_sync/audit.py.
Verifies downloaded files exist and match expected sizes.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from django import db

from .pixeldrain import PixeldrainClient, is_pixeldrain_not_found

logger = logging.getLogger(__name__)


def _build_name_index(traxdb_root: str) -> Dict[str, List[Dict[str, Any]]]:
    """Map basename -> list of {path, size} hits across traxdb_root."""
    idx: Dict[str, List[Dict[str, Any]]] = {}
    for base, _, files in os.walk(traxdb_root):
        for name in files:
            if name.startswith("."):
                continue
            p = os.path.join(base, name)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            idx.setdefault(name, []).append({"path": p, "size": sz})
    return idx


def _pick_dest_dir(traxdb_root: str, inferred_date: Optional[str], list_id: str) -> str:
    if inferred_date:
        return os.path.join(traxdb_root, inferred_date)
    return os.path.join(traxdb_root, "_inbox", list_id)


def run_audit(operation_id: int, sync_report_path: Optional[str] = None):
    """
    Run file audit in a background thread.

    If sync_report_path is provided, reads links from the JSON report (legacy mode).
    Otherwise, audits ScrapedFolder records from the DB.
    """
    from ..models import TraxDBOperation, ScrapedFolder

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

        # Determine which links to audit
        if sync_report_path and os.path.exists(sync_report_path):
            with open(sync_report_path, 'r', encoding='utf-8') as f:
                sync_report = json.load(f)
            links = sync_report.get('links_found') or sync_report.get('links_new') or []
        else:
            # Native mode: audit all downloaded folders
            downloaded_folders = ScrapedFolder.objects.filter(download_status='downloaded')
            links = [
                {
                    'list_id': f.folder_id,
                    'pixeldrain_url': f.pixeldrain_url,
                    'inferred_date': f.inferred_date,
                }
                for f in downloaded_folders
            ]

        # Build name index for global search
        name_index = _build_name_index(traxdb_root)

        audit_lists = []
        dead_links = []
        errors = []
        summary = {
            'lists_total': 0,
            'files_total': 0,
            'files_ok': 0,
            'files_missing': 0,
            'files_size_mismatch': 0,
        }

        for l in links:
            list_id = str(l.get('list_id') or '').strip()
            if not list_id:
                continue

            inferred_date = l.get('inferred_date')
            dest_dir = _pick_dest_dir(traxdb_root, inferred_date, list_id)

            try:
                files = list(client.iter_list_files(list_id))
                item = {
                    'list_id': list_id,
                    'pixeldrain_url': l.get('pixeldrain_url'),
                    'inferred_date': inferred_date,
                    'dest_dir': dest_dir,
                    'files': [],
                }

                for pf in files:
                    expected_path = os.path.join(dest_dir, os.path.basename(pf.name))
                    status = 'missing'
                    actual_path = None
                    actual_size = None

                    if os.path.exists(expected_path):
                        actual_path = expected_path
                        actual_size = os.path.getsize(expected_path)
                        if pf.size is None or actual_size == pf.size:
                            status = 'ok'
                        else:
                            status = 'size_mismatch'
                    elif pf.name in name_index:
                        hits = name_index[pf.name]
                        chosen = hits[0]
                        if pf.size is not None:
                            for h in hits:
                                if h['size'] == pf.size:
                                    chosen = h
                                    break
                        actual_path = chosen['path']
                        actual_size = chosen['size']
                        if pf.size is None or actual_size == pf.size:
                            status = 'ok_elsewhere'
                        else:
                            status = 'size_mismatch_elsewhere'

                    item['files'].append({
                        'name': pf.name,
                        'file_id': pf.id,
                        'expected_size': pf.size,
                        'expected_path': expected_path,
                        'status': status,
                        'actual_path': actual_path,
                        'actual_size': actual_size,
                    })

                audit_lists.append(item)
            except Exception as e:
                if is_pixeldrain_not_found(e):
                    dead_links.append({'list_id': list_id, 'error': repr(e)})
                else:
                    errors.append({'list_id': list_id, 'error': repr(e)})

        # Compute summary
        summary['lists_total'] = len(audit_lists)
        for lst in audit_lists:
            for f in lst['files']:
                summary['files_total'] += 1
                st = f['status']
                if st in ('ok', 'ok_elsewhere'):
                    summary['files_ok'] += 1
                elif st in ('size_mismatch', 'size_mismatch_elsewhere'):
                    summary['files_size_mismatch'] += 1
                else:
                    summary['files_missing'] += 1

        op.summary = {
            **summary,
            'errors_count': len(errors),
            'dead_links_count': len(dead_links),
            'lists': audit_lists,
            'dead_links': dead_links,
            'errors': errors,
        }

        op.status = 'completed' if not errors else 'failed'
        if errors:
            op.error_message = f'{len(errors)} errors during audit'
        op.save()
        logger.info(f'Audit #{operation_id} finished: {op.status}')

    except Exception as e:
        logger.exception(f'Audit #{operation_id} crashed')
        try:
            op = TraxDBOperation.objects.get(id=operation_id)
            op.status = 'failed'
            op.error_message = str(e)
            op.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()
