"""Bounded source-audio recognition shared by the VPS worker and Mac connector."""
import asyncio
import subprocess
import threading
import time

_PROVIDER_LOCK = threading.Lock()
_PROVIDER_NEXT = 0.0
from pathlib import Path


class RecognitionError(Exception):
    pass


async def recognize(video, directory, ffmpeg):
    from shazamio import Shazam
    candidates = []
    async with Shazam() as shazam:
        for offset in (0, 30, 60):
            sample = Path(directory) / 'sample.wav'
            result = subprocess.run([ffmpeg, '-v', 'error', '-y', '-ss', str(offset), '-protocol_whitelist', 'file,pipe', '-i', str(video),
                                    '-t', '20', '-vn', '-ac', '1', '-ar', '44100', str(sample)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
            if result.returncode != 0:
                raise RecognitionError('error')
            if not sample.exists() or sample.stat().st_size < 1000:
                if offset == 0: raise RecognitionError('error')
                break
            response = await asyncio.wait_for(shazam.recognize(str(sample)), timeout=40)
            track = response.get('track')
            if track:
                candidate = {'artist': track.get('subtitle', '')[:500], 'title': track.get('title', '')[:500]}
                if all(candidate.values()) and candidate not in candidates: candidates.append(candidate)
    return candidates


def explicit_candidates(caption, position, comments=(), *, media_count=None):
    """Return only source-associated IDs; conflicting comment cues need audio.

    media_count is the full carousel size only when every child is a video.
    None retains the legacy caption parser for callers without post context.
    Comment numbering alone never establishes a carousel association.
    """
    import re
    from unicodedata import normalize

    candidates, records = [], []
    sources = [('caption', '', caption)] + [
        ('comment', c['id'], c['text']) for c in comments[:20]]
    pattern = re.compile(
        r'^(?:(?:track(?:\s+id)?|id)\s*[:=]\s*|(?P<number>\d{1,2})[.)]\s+)'
        r'(?P<artist>[^\n]{1,500}?)\s+[–—-]\s+(?P<title>[^\n]{1,500})$', re.I)
    slide = re.compile(r'^(?:slide|video)\s+(\d{1,2})\s*[:.)-]\s*(?:id\s*:\s*)?', re.I)
    for source, comment_id, text in sources:
        lines = normalize('NFC', text[:10000]).splitlines()
        numbered = [pattern.fullmatch(line.strip()) for line in lines]
        numbers = [int(m['number']) for m in numbered if m and m['number']]
        mapped = (source == 'caption' and
                  (media_count is None or (media_count > 0 and sorted(numbers) == list(range(1, media_count + 1)))))
        for line in lines:
            line = line.strip()
            slot = slide.match(line)
            parsed = 'ID: ' + line[slot.end():] if slot else line
            match = pattern.fullmatch(parsed)
            if not match or '?' in line: continue
            if slot and int(slot[1]) != position + 1: continue
            if match['number'] and (not mapped or int(match['number']) != position + 1): continue
            artist, title = match['artist'].strip(), match['title'].strip()
            if not artist or not title: continue
            candidate = {'artist': artist, 'title': title}
            if not any(all(' '.join(c[k].casefold().split()) == ' '.join(candidate[k].casefold().split())
                           for k in candidate) for c in candidates) and len(candidates) < 2:
                candidates.append(candidate)
            if len(records) < 10:
                records.append({'source': source, 'comment_id': comment_id,
                                'text': line[:1100], 'candidate': candidate})
    # Disagreement is evidence for review, never a reason to skip Shazam.
    return (candidates if len(candidates) == 1 else []), records


def _caption_release_code(caption, catalog=''):
    """Extract one catalog field or conservative common release caption code.

    A leading bracket is an explicit catalog position. Bare codes/hashtags
    need an immediate release predicate; generic tags alone prove nothing.
    Any other catalog-shaped token makes the caption ambiguous.
    """
    import re

    token = r'[a-zA-Z]{2,12}[0-9]{2,6}'
    codes = {m[0].upper() for m in re.finditer(
        r'(?<![\w/@.-])' + token + r'(?![\w-])', caption)}
    if catalog:
        codes.add(catalog.upper())
    if len(codes) != 1:
        return ''
    if catalog:
        return catalog
    code = codes.pop()
    for line in caption.splitlines():
        line = line.strip()
        if '?' in line:
            continue
        bracket = re.match(r'^\[(' + token + r')\](?:\s|$)', line)
        prose = re.match(
            r'^#?(' + token + r')\s+(?:is\s+)?'
            r'(?:in[- ]stock|out\s+now|available\s+now|up\s+on\s+bandcamp)\b', line, re.I)
        match = bracket or prose
        if match and match[1].upper() == code:
            return code
    return ''


def release_candidates(caption, position, media_count):
    """Resolve an explicit catalog code, never a release title as a recording.

    Keyless MusicBrainz lookup, at most search + one release request. Only a
    unique exact catalog/label hit and an explicit track selector or complete
    all-video carousel can establish association. No previews are identities.
    """
    import re
    import time
    import requests
    from wanted.services.preview import _same_name

    fields = {}
    carousel_order = False
    for line in caption[:10000].splitlines():
        if re.fullmatch(r'\s*carousel\s*:\s*tracklist order\s*', line, re.I):
            carousel_order = True
        match = re.fullmatch(r'\s*(catalog(?:ue)?(?:\s+(?:number|no\.?))?|cat(?:no)?\.?|label|track)\s*[:#]\s*(.{1,100})\s*', line, re.I)
        if match:
            key = match[1].lower()
            key = 'catalog' if key.startswith('cat') else key
            value = match[2].strip()
            if key in fields and fields[key] != value:
                return [], []
            fields[key] = value
    code = _caption_release_code(caption[:10000], fields.get('catalog', ''))
    if not re.fullmatch(r'(?=.*[a-zA-Z])(?=.*[0-9])[a-zA-Z0-9 ._-]{2,60}', code):
        return [], []
    if media_count < 1 or (fields.get('track') and media_count != 1):
        return [], []
    # Quote only a restricted catalog token; label values are escaped for Lucene.
    query = 'catno:"' + code + '"'
    if fields.get('label'):
        label = fields['label'].replace('\\', '\\\\').replace('"', '\\"')
        query += ' AND label:"' + label + '"'
    try:
        search = _release_json('', {'query': query, 'limit': 3, 'fmt': 'json'})
        hits = search.get('releases', [])
        if search.get('count') != 1 or len(hits) != 1:
            return [], []
        release_id = hits[0]['id']
        if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', release_id):
            return [], []
        time.sleep(1.1)  # MusicBrainz permits one request per second.
        release = _release_json(release_id, {'inc': 'recordings+artist-credits+labels', 'fmt': 'json'})
        if release.get('id') != release_id:
            return [], []
        labels = release.get('label-info', [])
        if not any(_same_name(code, x.get('catalog-number', '')) and
                   (not fields.get('label') or _same_name(fields['label'], x.get('label', {}).get('name', '')))
                   for x in labels):
            return [], []
        media = release.get('media', [])
        if len(media) != 1:
            return [], []
        tracks = media[0].get('tracks', [])
        if not tracks or len(tracks) > 20 or media[0].get('track-count') != len(tracks):
            return [], []
        selector = fields.get('track')
        if selector:
            selected = [t for t in tracks if _same_name(selector, str(t.get('number', '')))]
        elif (media_count == 1 or carousel_order) and len(tracks) == media_count and 0 <= position < media_count:
            selected = [tracks[position]]
        else:
            return [], []
        if len(selected) != 1:
            return [], []
        track = selected[0]
        recording = track.get('recording', {})
        title = track.get('title') or recording.get('title', '')
        if not _same_name(title, recording.get('title', '')):
            return [], []
        # A recording qualifier missing from the title would lose its version.
        if recording.get('disambiguation') and recording['disambiguation'].casefold() not in title.casefold():
            return [], []
        credits = track.get('artist-credit') or recording.get('artist-credit') or []
        artist = ''.join(c['name'] + c.get('joinphrase', '') for c in credits).strip()
        if (not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', recording.get('id', '')) or not artist or not title or max(len(artist), len(title)) > 500
                or artist.casefold() in ('various artists', 'unknown artist', '[unknown]')):
            return [], []
        candidate = {'artist': artist, 'title': title}
        # Evidence fits the existing private record schema; no migration or hook.
        record = {'source': 'caption', 'comment_id': '', 'candidate': candidate,
                  'text': ('Catalog: ' + code + '; https://musicbrainz.org/release/' + release_id +
                           '; track ' + str(track.get('number', '')) + '; recording ' + recording['id'] +
                           '; association: ' + ('explicit Track selector' if selector else
                               'single-track video' if media_count == 1 else
                               'declared carousel tracklist order; slide ' + str(position + 1)))[:1100]}
        return [candidate], [record]
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
        return [], []


def _release_json(release_id, params):
    """Fixed origin, capped body, no credentials, redirects or retries.

    A busy process fails open to audio rather than queueing provider requests.
    Independent VPS processes may receive 503/rate limits; those also fall back.
    """
    import json
    import requests
    global _PROVIDER_NEXT
    if not _PROVIDER_LOCK.acquire(blocking=False):
        return {}
    try:
        if time.monotonic() < _PROVIDER_NEXT:
            return {}
        _PROVIDER_NEXT = time.monotonic() + 1.1
        with requests.get('https://musicbrainz.org/ws/2/release/' + release_id,
                          params=params, headers={'User-Agent': 'OCDJ/1.0 (https://github.com/IvPalmer/ocdj)'},
                          timeout=(3, 5), allow_redirects=False, stream=True) as response:
            response.raise_for_status()
            if response.status_code != 200:
                return {}
            body = bytearray()
            deadline = time.monotonic() + 8
            for chunk in response.iter_content(8192):
                body.extend(chunk)
                if len(body) > 512 * 1024 or time.monotonic() > deadline:
                    return {}
            return json.loads(body)
    finally:
        _PROVIDER_LOCK.release()
