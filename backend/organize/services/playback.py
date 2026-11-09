"""Read-only browser playback of a pipeline row, confined to owned roots."""
import os
import stat

from core.services.config import get_config
from .refresh import resolve_artifact_path

# Serve only browser audio containers; never HTML, playlists or arbitrary files.
AUDIO_TYPES = {
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
    '.aac': 'audio/aac', '.flac': 'audio/flac', '.ogg': 'audio/ogg',
    '.opus': 'audio/ogg',
}


class PlaybackUnavailable(Exception):
    pass


def open_audio(item):
    """Resolve existing pipeline bytes, then walk without following symlinks.

    Directory-relative opens fence ancestor replacement races too. Never accept
    a browser path; work_path/current_path belong to the selected database row.
    """
    if item.archive_state == 'archived':
        raise PlaybackUnavailable('File archived on Mac')
    path = resolve_artifact_path(item)
    if not path:
        raise PlaybackUnavailable('File unavailable on server')
    mime = AUDIO_TYPES.get(os.path.splitext(path)[1].lower())
    if not mime:
        raise PlaybackUnavailable('Format unavailable for browser playback')
    roots = [get_config('SOULSEEK_DOWNLOAD_ROOT') or '', os.environ.get('OCDJ_PUBLISH_ROOT', '')]
    # Reject traversal/aliases rather than normalizing them into allowed paths.
    parts = path.split('/')
    if not os.path.isabs(path) or any(p in ('', '.', '..') for p in parts[1:]):
        raise PlaybackUnavailable('File unavailable on server')
    if not any(path.startswith(os.path.abspath(root).rstrip('/') + '/') for root in roots if root and os.path.abspath(root) != '/'):
        raise PlaybackUnavailable('File unavailable on server')
    fd = None
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        for index, part in enumerate(parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(parts) - 2:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            raise PlaybackUnavailable('File unavailable on server')
        handle = os.fdopen(fd, 'rb')
        fd = None
        return handle, mime, info.st_size
    except (OSError, ValueError):
        raise PlaybackUnavailable('File unavailable on server') from None
    finally:
        if fd is not None:
            os.close(fd)
