"""
Services layer for TraxDB — wraps existing CLI tools in tools/traxdb_sync/.

Each function is designed to be called from a background thread.
"""
import json
import logging
import os
import sys
from datetime import datetime

from django import db

logger = logging.getLogger(__name__)

# ── Ensure traxdb_sync is importable ──────────────────────────

_tools_dir = os.environ.get('TOOLS_DIR', '/app/tools')
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)


def _reports_dir():
    """Ensure the reports directory exists and return its path."""
    traxdb_root = os.environ.get('TRAXDB_ROOT', '/music/Electronic/ID3/traxdb')
    d = os.path.join(traxdb_root, '_reports')
    os.makedirs(d, exist_ok=True)
    return d


def _timestamp():
    return datetime.now().strftime('%Y%m%d-%H%M%S')


def _read_json(path):
    """Read a JSON file, return empty dict on any error."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ── Background workers ────────────────────────────────────────

def run_sync(operation_id, max_pages=50):
    """Run TraxDB sync (blog scrape) in a background thread."""
    from .models import TraxDBOperation

    try:
        op = TraxDBOperation.objects.get(id=operation_id)
        op.status = 'running'
        op.save()

        traxdb_root = os.environ.get('TRAXDB_ROOT', '/music/Electronic/ID3/traxdb')
        start_url = os.environ.get('TRAXDB_START_URL', '')
        pixeldrain_key = os.environ.get('PIXELDRAIN_API_KEY', '')
        cookies = os.environ.get('TRAXDB_COOKIES', '')

        if not start_url:
            op.status = 'failed'
            op.error_message = 'TRAXDB_START_URL not configured'
            op.save()
            return

        report_path = os.path.join(_reports_dir(), f'sync_{_timestamp()}.json')
        op.report_path = report_path
        op.save()

        from traxdb_sync.sync import main as sync_main

        argv = [
            '--traxdb-root', traxdb_root,
            '--traxdb-start-url', start_url,
            '--max-pages', str(max_pages),
            '--report-path', report_path,
            '--plan-files',
        ]
        if pixeldrain_key:
            argv += ['--pixeldrain-api-key', pixeldrain_key]
        if cookies:
            argv += ['--traxdb-cookies', cookies]

        exit_code = sync_main(argv)

        # Read and summarize report
        report = _read_json(report_path)
        links_found = report.get('links_found', [])
        links_new = report.get('links_new', [])

        op.summary = {
            'links_found_count': len(links_found),
            'links_new_count': len(links_new),
            'links_skipped_by_cutoff_date': len(report.get('links_skipped_by_cutoff_date', [])),
            'pages_scraped': report.get('config_used', {}).get('max_pages', max_pages),
            'errors_count': len(report.get('errors', [])),
            'exit_code': exit_code,
            # Full details for the UI
            'links_found': links_found,
            'links_new': links_new,
            'errors': report.get('errors', []),
        }

        # Sync may exit non-zero when --plan-files hits dead Pixeldrain links,
        # but if we got real data that's still a usable sync.
        if exit_code == 0 or len(links_found) > 0:
            op.status = 'completed'
            if exit_code != 0:
                op.error_message = f'{len(report.get("errors", []))} errors (dead links during plan-files)'
        else:
            op.status = 'failed'
            op.error_message = f'sync.py exited with code {exit_code}'
            errors = report.get('errors', [])
            if errors:
                op.error_message += f' — {len(errors)} errors'

        op.save()
        logger.info(f'Sync #{operation_id} finished: {op.status}')

    except Exception as e:
        logger.exception(f'Sync #{operation_id} crashed')
        try:
            op = TraxDBOperation.objects.get(id=operation_id)
            op.status = 'failed'
            op.error_message = str(e)
            op.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()


def run_download(operation_id, sync_report_path, links_key='links_new'):
    """Run TraxDB download (Pixeldrain batch download) in a background thread."""
    from .models import TraxDBOperation

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

        report_path = os.path.join(_reports_dir(), f'download_{_timestamp()}.json')
        progress_path = os.path.join(_reports_dir(), f'download_{_timestamp()}_progress.json')
        op.report_path = report_path
        op.progress_path = progress_path
        op.save()

        from traxdb_sync.download_from_report import main as dl_main

        argv = [
            '--traxdb-root', traxdb_root,
            '--report', sync_report_path,
            '--links-key', links_key,
            '--report-path', report_path,
            '--progress-path', progress_path,
            '--progress-interval-s', '5',
            '--quiet',
        ]

        exit_code = dl_main(argv)

        # Read and summarize report
        report = _read_json(report_path)
        summary = report.get('summary', {})

        op.summary = {
            'lists_total': summary.get('lists_total', 0),
            'lists_completed': summary.get('lists_completed', 0),
            'lists_dead': summary.get('lists_dead', 0),
            'files_total': summary.get('files_total', 0),
            'files_completed': summary.get('files_completed', 0),
            'bytes_downloaded': summary.get('bytes_downloaded', 0),
            'errors_count': len(report.get('errors', [])),
            'dead_links_count': len(report.get('dead_links', [])),
            'exit_code': exit_code,
            # Full details for the UI
            'lists': report.get('lists', []),
            'dead_links': report.get('dead_links', []),
            'errors': report.get('errors', []),
        }

        if exit_code == 0:
            op.status = 'completed'
        else:
            op.status = 'failed'
            op.error_message = f'download_from_report.py exited with code {exit_code}'

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
        # Clean up lock file if it exists
        try:
            traxdb_root = os.environ.get('TRAXDB_ROOT', '/music/Electronic/ID3/traxdb')
            lock_path = os.path.join(traxdb_root, '.download_from_report.lock')
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            pass
        db.connections.close_all()


def run_audit(operation_id, sync_report_path):
    """Run TraxDB audit (verify local files against Pixeldrain) in a background thread."""
    from .models import TraxDBOperation

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

        report_path = os.path.join(_reports_dir(), f'audit_{_timestamp()}.json')
        op.report_path = report_path
        op.save()

        from traxdb_sync.audit import main as audit_main

        argv = [
            '--traxdb-root', traxdb_root,
            '--report', sync_report_path,
            '--report-path', report_path,
            '--global-search-by-name',
        ]

        exit_code = audit_main(argv)

        # Read and summarize report
        report = _read_json(report_path)
        summary = report.get('summary', {})

        op.summary = {
            'lists_total': summary.get('lists_total', 0),
            'files_total': summary.get('files_total', 0),
            'files_ok': summary.get('files_ok', 0),
            'files_missing': summary.get('files_missing', 0),
            'files_size_mismatch': summary.get('files_size_mismatch', 0),
            'errors_count': len(report.get('errors', [])),
            'dead_links_count': len(report.get('dead_links', [])),
            'exit_code': exit_code,
            # Full details for the UI
            'lists': report.get('lists', []),
            'dead_links': report.get('dead_links', []),
            'errors': report.get('errors', []),
        }

        if exit_code == 0:
            op.status = 'completed'
        else:
            op.status = 'failed'
            op.error_message = f'audit.py exited with code {exit_code}'

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
