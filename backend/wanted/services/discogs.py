import logging
import os
import threading

from django import db
from django.conf import settings

from wanted.models import ImportOperation
from .dedup import check_duplicates

logger = logging.getLogger(__name__)


def _get_config():
    return {
        'token': getattr(settings, 'DISCOGS_PERSONAL_TOKEN', '') or os.environ.get('DISCOGS_PERSONAL_TOKEN', ''),
        'username': getattr(settings, 'DISCOGS_USERNAME', '') or os.environ.get('DISCOGS_USERNAME', ''),
    }


def run_discogs_import(operation_id):
    """Fetch a Discogs wantlist and parse releases. Runs in a background thread."""
    thread = threading.Thread(
        target=_discogs_worker,
        args=(operation_id,),
        daemon=True,
    )
    thread.start()


def _discogs_worker(operation_id):
    try:
        op = ImportOperation.objects.get(pk=operation_id)
        op.status = 'fetching'
        op.save()

        import discogs_client

        config = _get_config()
        if not config['token'] or not config['username']:
            raise ValueError('Discogs token or username not configured')

        d = discogs_client.Client('DJTools/2.0', user_token=config['token'])
        user = d.identity()
        wantlist = user.wantlist

        import re

        tracks = []
        for want in wantlist:
            release = want.release
            artists = ', '.join(a.name for a in release.artists) if release.artists else ''
            # Clean "Artist (N)" suffixes Discogs adds
            if artists:
                artists = re.sub(r'\s*\(\d+\)$', '', artists)

            labels = release.labels if release.labels else []
            label_name = labels[0].name if labels else ''
            catno = labels[0].catno if labels else ''

            tracks.append({
                'artist': artists,
                'title': release.title,
                'release_name': release.title,
                'label': label_name,
                'catalog_number': catno,
                'raw_title': f"{artists} - {release.title}",
                'source_url': f"https://www.discogs.com/release/{release.id}",
            })

        tracks = check_duplicates(tracks)

        duplicates = sum(1 for t in tracks if t.get('is_duplicate'))
        op.preview_data = tracks
        op.total_found = len(tracks)
        op.duplicates_found = duplicates
        op.status = 'previewing'
        op.save()

    except Exception as e:
        logger.exception(f'Discogs import failed for operation {operation_id}')
        try:
            op = ImportOperation.objects.get(pk=operation_id)
            op.status = 'failed'
            op.error_message = str(e)
            op.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()
