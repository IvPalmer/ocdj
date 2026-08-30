"""Turn Shazams into WantedItems, and pull them from Spotify.

## Why Spotify and not Apple Music

Apple exposes no way to read your own Shazam library — verified, not assumed:
the macOS Shazam app is App Store build 2.11 from Aug 2022 with an empty Core
Data store, `com.apple.shazamd` syncs over CloudKit but writes nothing readable
to disk, shazam.com/myshazam is a 404, there is no history API, and Shortcuts
ships exactly two Shazam actions on macOS *and* iOS, neither of which reads the
library.

What Shazam does offer is syncing each identification into one streaming
service's playlist — one service, never both. Apple Music was tried first and
was the wrong choice: on a Mac signed into iCloud Music Library those tracks
land in Music.app, which here is the finished, hand-curated library. A feed of
unvetted club IDs does not belong in the destination.

Spotify has the same mechanism and none of that cost, because nothing here
treats Spotify as a library. It also removes the Mac from the path entirely —
this runs on the server, so it no longer depends on a laptop being awake.

The hole this does not close: only tracks Spotify has in catalogue arrive.
Promos and white labels get Shazamed and never show up. Nothing available today
fixes that.
"""
from __future__ import annotations

import logging
import re

from django.utils import timezone

from wanted.models import WantedItem, WantedSource
from .dedup import _normalize

logger = logging.getLogger(__name__)

# Shazam names the playlist per locale; it also recreates it under the default
# name if renamed, so match loosely rather than pinning one string.
PLAYLIST_RE = re.compile(r'shazam', re.IGNORECASE)


def get_source() -> WantedSource:
    source, _ = WantedSource.objects.get_or_create(
        source_type='shazam', defaults={'name': 'Shazam', 'active': True},
    )
    return source


def ingest(items) -> dict:
    """Create WantedItems for Shazams that aren't already tracked.

    Shared by the bearer-authed HTTP endpoint and the Spotify poller, so both
    entry points dedupe identically. Callers have no reliable cursor of their
    own — a playlist read hands back everything every time — so the server
    decides what is new.
    """
    source = get_source()
    created, skipped = [], 0

    # One read of what we already have, rather than a query per incoming track.
    known = {
        (_normalize(a), _normalize(t))
        for a, t in WantedItem.objects.values_list('artist', 'title')
    }

    for raw in items:
        if not isinstance(raw, dict):
            continue
        artist = (raw.get('artist') or '').strip()[:500]
        title = (raw.get('title') or '').strip()[:500]
        if not artist and not title:
            skipped += 1
            continue

        key = (_normalize(artist), _normalize(title))
        if key in known:
            skipped += 1
            continue
        known.add(key)

        note_bits = []
        if raw.get('shazamed_at'):
            note_bits.append(f"shazamed {raw['shazamed_at']}")
        if raw.get('device'):
            note_bits.append(f"on {raw['device']}")
        if raw.get('isrc'):
            note_bits.append(f"ISRC {raw['isrc']}")

        item = WantedItem.objects.create(
            artist=artist,
            title=title,
            release_name=(raw.get('album') or '').strip()[:500],
            source=source,
            identified_via='shazam',
            notes=' · '.join(note_bits),
            status='identified',
        )
        created.append({'id': item.id, 'artist': artist, 'title': title})

    source.last_checked = timezone.now()
    source.save(update_fields=['last_checked'])
    return {'created': created, 'created_count': len(created), 'skipped': skipped}


def _find_playlist(sp):
    """The Shazam playlist in the user's own Spotify account."""
    offset = 0
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        for pl in page.get('items') or []:
            if pl and PLAYLIST_RE.search(pl.get('name') or ''):
                return pl
        if not page.get('next'):
            return None
        offset += 50


def sync_from_spotify(seed: bool = False) -> dict:
    """Read the Shazam playlist and ingest anything added since the cursor.

    `seed=True` advances the cursor to the newest item without ingesting: the
    switch-on move, because connecting Shazam to Spotify backfills the whole
    library into the playlist and the point of this feed is what gets Shazamed
    from here on.
    """
    from .spotify import _get_sp

    source = get_source()
    sp, _ = _get_sp()

    playlist = _find_playlist(sp)
    if not playlist:
        return {'error': 'no Shazam playlist in this Spotify account — connect '
                         'Spotify inside the Shazam app first',
                'created_count': 0}

    # Spotify pages oldest-first, and `added_at` is the only ordering that
    # survives the operator reordering or deduping the playlist by hand.
    rows, offset = [], 0
    while True:
        page = sp.playlist_items(
            playlist['id'], limit=100, offset=offset,
            fields='items(added_at,track(name,artists(name),album(name),external_ids)),next',
        )
        for it in page.get('items') or []:
            track = (it or {}).get('track') or {}
            if not track.get('name'):
                continue        # local file or unavailable track
            rows.append({
                'added_at': it.get('added_at') or '',
                'artist': ', '.join(a.get('name', '') for a in (track.get('artists') or [])),
                'title': track.get('name', ''),
                'album': (track.get('album') or {}).get('name', ''),
                'isrc': (track.get('external_ids') or {}).get('isrc', ''),
                'device': 'Shazam → Spotify',
            })
        if not page.get('next'):
            break
        offset += 100

    if not rows:
        source.last_checked = timezone.now()
        source.save(update_fields=['last_checked'])
        return {'created_count': 0, 'skipped': 0, 'playlist_total': 0}

    newest = max(r['added_at'] for r in rows)

    if seed:
        source.cursor = newest
        source.last_checked = timezone.now()
        source.save(update_fields=['cursor', 'last_checked'])
        logger.info('shazam/spotify: seeded cursor at %s (%d existing tracks left out)',
                    newest, len(rows))
        return {'seeded': True, 'cursor': newest, 'playlist_total': len(rows),
                'created_count': 0}

    cursor = source.cursor or ''
    fresh = [r for r in rows if r['added_at'] > cursor] if cursor else rows
    for r in fresh:
        r['shazamed_at'] = r.pop('added_at')
    for r in rows:
        r.pop('added_at', None)

    result = ingest(fresh)
    # Advance only after the rows are stored, so a crash mid-ingest replays
    # instead of skipping.
    source.cursor = newest
    source.save(update_fields=['cursor'])

    result['playlist_total'] = len(rows)
    logger.info('shazam/spotify: %d new, %d already known, playlist has %d',
                result['created_count'], result['skipped'], len(rows))
    return result
