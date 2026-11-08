"""Bounded source-audio recognition shared by the VPS worker and Mac connector."""
import asyncio
import subprocess
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


def explicit_candidates(caption, position, comments=()):
    """Conservative line parser. Numbered track lists address carousel slots.

    A caption's single explicit ID is source evidence; comments are suggestions.
    Keep mix/version strings intact and never treat prose with a dash as an ID.
    """
    import re
    candidates, records = [], []
    sources = [('caption', '', caption)] + [('comment', c['id'], c['text']) for c in comments]
    pattern = re.compile(r'^(?:(?:track|id)\s*:\s*|(?P<number>\d{1,2})[.)]\s+)(?P<artist>[^\n]{1,500}?)\s+[–—-]\s+(?P<title>[^\n]{1,500})$', re.I)
    for source, comment_id, text in sources:
        for line in text.splitlines():
            match = pattern.fullmatch(line.strip())
            if not match: continue
            if match['number'] and int(match['number']) != position + 1: continue
            artist, title = match['artist'].strip(), match['title'].strip()
            if not artist or not title: continue
            candidate = {'artist': artist, 'title': title}
            if candidate not in candidates:
                candidates.append(candidate)
                records.append({'source': source, 'comment_id': comment_id,
                                'text': line.strip()[:1100], 'candidate': candidate})
            if len(candidates) == 10: return candidates, records
    return candidates, records
