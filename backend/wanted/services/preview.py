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
from unicodedata import normalize

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 12


def _clean(*parts: str) -> str:
    return ' '.join(p.strip() for p in parts if p and p.strip())


def _same_name(expected: str, actual: str) -> bool:
    return ' '.join(normalize('NFC', expected).casefold().split()) == ' '.join(
        normalize('NFC', actual).casefold().split()
    )


def _itunes(artist: str, title: str, exact: bool) -> Optional[Tuple[str, str, str]]:
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
        if not url:
            continue
        if exact and (not url.startswith('https://')
                      or not _same_name(artist, res.get('artistName') or '')
                      or not _same_name(title, res.get('trackName') or '')):
            continue
        return url, 'itunes', res.get('trackViewUrl') or ''
    return None


def _deezer(artist: str, title: str, exact: bool) -> Optional[Tuple[str, str, str]]:
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
        if not url:
            continue
        if exact and (not url.startswith('https://')
                      or not _same_name(artist, (res.get('artist') or {}).get('name') or '')
                      or not _same_name(title, res.get('title') or '')):
            continue
        return url, 'deezer', res.get('link') or ''
    return None


def _find_preview(artist: str, title: str, exact: bool) -> Tuple[str, str, str]:
    """Return (url, provider, catalog track URL), empty when nobody has it.

    Never raises: a preview is a convenience, and one flaky third party should
    not turn a row in a list into an error.
    """
    for fn in (_itunes, _deezer):
        try:
            hit = fn(artist, title, exact)
        except Exception as e:
            logger.warning('preview lookup via %s failed: %s',
                           fn.__name__.strip('_'), type(e).__name__)
            continue
        if hit:
            return hit
    return '', '', ''


def find_preview(artist: str, title: str) -> Tuple[str, str]:
    url, provider, _ = _find_preview(artist, title, exact=False)
    return url, provider


def find_exact_preview(artist: str, title: str) -> Tuple[str, str, str]:
    """Return only audio of the requested artist and track, including mix names."""
    url, provider, track_url = _find_preview(artist, title, exact=True)
    return url, provider, track_url if track_url.startswith('https://') else ''
