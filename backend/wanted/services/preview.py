"""Find a 30-second preview for a wanted track.

Spotify used to be the obvious source and no longer is: `preview_url` was
deprecated in November 2024 and comes back null for any app registered since.

So: iTunes Search first, Deezer second. iTunes leads because Apple owns Shazam
— a track that arrived here through the Shazam feed reached us *via* Apple's
catalogue, so Apple almost always has it. Deezer catches the rest, and both are
free, keyless, and return a plain audio URL the browser can play.

A miss is cached the same as a hit. Plenty of what this app tracks is promo or
white-label material neither service has ever heard of, and without recording
the miss every page render would re-ask two APIs the same dead question.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 12


def _clean(*parts: str) -> str:
    return ' '.join(p.strip() for p in parts if p and p.strip())


def _itunes(artist: str, title: str) -> Optional[Tuple[str, str]]:
    term = _clean(artist, title)
    if not term:
        return None
    r = requests.get(
        'https://itunes.apple.com/search',
        params={'term': term, 'entity': 'song', 'limit': 3},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    for res in (r.json().get('results') or []):
        url = res.get('previewUrl')
        if url:
            return url, 'itunes'
    return None


def _deezer(artist: str, title: str) -> Optional[Tuple[str, str]]:
    if not _clean(artist, title):
        return None
    # Deezer's field-qualified syntax beats a bare term here: a track called
    # "Clouds" is hopeless as free text but precise as artist:"Tish" track:…
    q = ' '.join(filter(None, [
        f'artist:"{artist}"' if artist else '',
        f'track:"{title}"' if title else '',
    ]))
    r = requests.get(
        'https://api.deezer.com/search', params={'q': q, 'limit': 3},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    for res in (r.json().get('data') or []):
        url = res.get('preview')
        if url:
            return url, 'deezer'
    return None


def find_preview(artist: str, title: str) -> Tuple[str, str]:
    """Return (url, provider). Both empty when nobody has it.

    Never raises: a preview is a convenience, and one flaky third party should
    not turn a row in a list into an error.
    """
    for fn in (_itunes, _deezer):
        try:
            hit = fn(artist, title)
        except Exception as e:
            logger.warning('preview lookup via %s failed for %r: %r',
                           fn.__name__.strip('_'), _clean(artist, title), e)
            continue
        if hit:
            return hit
    return '', ''
