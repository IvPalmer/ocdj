import logging
import re
import unicodedata

import requests
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# Discogs disambiguates same-named artists with a trailing " (N)" — e.g.
# "Vsan (2)". Strip it so it still matches the plain "Vsan" on the track.
_DISCOGS_DISAMBIG = re.compile(r'\s*\(\d+\)\s*$')

# Split a joined credit into individual artists: "A, B feat. C" → [A, B, C].
# Only strong, unambiguous separators — deliberately NOT " and "/" x "/"/"
# which appear inside real artist names (e.g. "AC/DC", "Above and Beyond").
_CREDIT_SPLIT = re.compile(
    r'\s*(?:,|&|;|\bfeat\b\.?|\bft\b\.?|\bvs\b\.?|\bpres\b\.?|\bwith\b)\s*',
    re.IGNORECASE,
)

# A compilation's release artist on Discogs — won't equal the track artist.
_VARIOUS = {'various', 'various artists', 'va', 'v/a'}


def _fold(s):
    """Lowercase, strip accents, trim — so 'Âme' compares equal to 'Ame'."""
    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()


def _norm_artist(name):
    return _fold(_DISCOGS_DISAMBIG.sub('', name or ''))


def _credits(name):
    """Individual normalized artist names from a joined credit string."""
    return [c for c in (_norm_artist(p) for p in _CREDIT_SPLIT.split(name or '')) if c]


def _is_various(name):
    return _fold(name) in _VARIOUS

# Minimum fuzzy score for a search result to be trusted as the same release.
# The whole point: a WRONG cover is worse than no cover, so we only embed art
# from a result whose artist (and, where available, title) actually matches
# the track. Obscure tracks with no real match get no artwork rather than a
# random one lifted off the first search hit.
_ARTIST_MATCH_MIN = 78
_TITLE_MATCH_MIN = 55


def _artist_ok(want_artist, got_artist):
    if not want_artist:
        return True  # nothing to check against — don't block
    if not got_artist:
        return False  # we have an artist to match but the result has none
    # Whole-string compare first (handles exact + Discogs "(N)" disambiguation).
    if fuzz.token_sort_ratio(_norm_artist(want_artist), _norm_artist(got_artist)) >= _ARTIST_MATCH_MIN:
        return True
    # Multi-artist / "feat." credits: accept when the wanted artist strongly
    # matches any single credit in the result ("Vsan" vs "Vsan, Guest") — the
    # whole-string ratio drops off with each extra collaborator otherwise.
    want_credits = _credits(want_artist) or [_norm_artist(want_artist)]
    got_credits = _credits(got_artist)
    return any(
        fuzz.ratio(wc, gc) >= _ARTIST_MATCH_MIN
        for wc in want_credits for gc in got_credits
    )


def _title_ok(want_title, got_text):
    if not want_title or not got_text:
        return True  # can't compare — rely on the artist check
    # got_text may be "Artist - Title"; partial_ratio tolerates the extra parts.
    return fuzz.partial_ratio(_fold(want_title), _fold(got_text)) >= _TITLE_MATCH_MIN


def fetch_artwork(artist, title, label='', catalog_number=''):
    """Try to fetch cover art. Discogs first, then Spotify. Returns bytes or None.

    Only returns art from a result verified to match the track — a wrong cover
    is worse than none.
    """
    image_bytes = _fetch_from_discogs(artist, title, label, catalog_number)
    if image_bytes:
        return image_bytes

    image_bytes = _fetch_from_spotify(artist, title)
    if image_bytes:
        return image_bytes

    return None


def _fetch_from_discogs(artist, title, label='', catalog_number=''):
    """Fetch artwork from Discogs — only from a release that matches the track."""
    try:
        from core.views import get_config
        token = get_config('DISCOGS_PERSONAL_TOKEN')
        if not token:
            return None

        import discogs_client
        d = discogs_client.Client('OCDJ/2.0', user_token=token)

        if catalog_number:
            results = d.search(catno=catalog_number, type='release')
        elif artist and title:
            results = d.search(f'{artist} {title}', type='release')
        else:
            return None

        # discogs_client paginated lists don't support Python slice syntax.
        from itertools import islice
        for result in islice(results, 5):
            # A catalog number is NOT globally unique across labels, and a
            # free-text search returns loose matches — so verify the release
            # is actually this artist before trusting its cover.
            r_artist = (
                ', '.join(a.name for a in result.artists)
                if hasattr(result, 'artists') and result.artists else ''
            )
            # No title check here: a Discogs result's title is the RELEASE/EP
            # name (e.g. "Entre Mans EP"), not the track ("Talaiot"), so it
            # legitimately differs. Artist match + the catalog-number search
            # already scope it to the right release.
            #
            # A compilation's release artist is "Various" and will never match
            # the track artist — but when we found the release by its (release-
            # specific) catalog number, that already pins the exact release, so
            # trust its cover instead of falling through to a Spotify guess.
            if _is_various(r_artist) and catalog_number:
                pass
            elif not _artist_ok(artist, r_artist):
                continue
            if hasattr(result, 'images') and result.images:
                img_url = result.images[0].get('uri') or result.images[0].get('resource_url')
                if img_url:
                    # Discogs image CDN 403s without the token — this is why
                    # Discogs artwork silently never worked and everything fell
                    # through to Spotify's loose matches.
                    resp = requests.get(img_url, timeout=15, headers={
                        'Authorization': f'Discogs token={token}',
                        'User-Agent': 'OCDJ/2.0',
                    })
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        return resp.content
    except Exception as e:
        logger.warning(f"Discogs artwork fetch failed: {e}")

    return None


def _fetch_from_spotify(artist, title):
    """Fetch album artwork from Spotify — only from a track that matches."""
    try:
        from core.views import get_config
        client_id = get_config('SPOTIFY_CLIENT_ID')
        client_secret = get_config('SPOTIFY_CLIENT_SECRET')
        if not client_id or not client_secret:
            return None

        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
        ))

        query = f'{artist} {title}' if artist else title
        results = sp.search(q=query, type='track', limit=5)

        for track in results.get('tracks', {}).get('items', []):
            t_artist = ', '.join(a.get('name', '') for a in track.get('artists', []))
            t_title = track.get('name', '')
            # Spotify fuzzy-matches hard; verify before trusting the cover.
            if not _artist_ok(artist, t_artist):
                continue
            if not _title_ok(title, t_title):
                continue
            images = track.get('album', {}).get('images', [])
            if images:
                img_url = images[0].get('url')  # largest
                if img_url:
                    resp = requests.get(img_url, timeout=15)
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        return resp.content
    except Exception as e:
        logger.warning(f"Spotify artwork fetch failed: {e}")

    return None


def embed_artwork(filepath, image_bytes):
    """Embed artwork into an audio file."""
    import os
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import ID3, APIC

    ext = os.path.splitext(filepath)[1].lower()

    try:
        if ext == '.flac':
            flac = FLAC(filepath)
            pic = Picture()
            pic.type = 3  # Cover (front)
            pic.mime = 'image/jpeg'
            pic.desc = 'Cover'
            pic.data = image_bytes
            flac.clear_pictures()
            flac.add_picture(pic)
            flac.save()

        elif ext in ('.mp3', '.aiff', '.aif'):
            audio = mutagen.File(filepath)
            if audio.tags is None:
                audio.add_tags()

            # Drop any existing cover first. add() is keyed by APIC desc, so a
            # source cover stored under a different desc would survive as a
            # second frame and players might show the stale one — mirror FLAC's
            # clear_pictures() so exactly one cover remains.
            audio.tags.delall('APIC')
            audio.tags.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,  # Cover (front)
                desc='Cover',
                data=image_bytes,
            ))
            audio.save()

        else:
            logger.info(f"Artwork embedding not supported for {ext}")
    except Exception as e:
        logger.warning(f"Failed to embed artwork in {filepath}: {e}")
