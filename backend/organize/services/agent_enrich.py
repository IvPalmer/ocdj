"""Agent-backed metadata enrichment for pipeline items.

Triggered between stages `tagged` and `renamed` when the file's raw tags
look like typical Soulseek compilation garbage (track number baked into
title, catalog name in artist field, multiple ' - ' separators, etc.) AND
Discogs/MusicBrainz didn't already correct it.

Uses claude-agent-sdk which spawns the `claude` CLI under the hood and
authenticates via CLAUDE_CODE_OAUTH_TOKEN (Max subscription, no API key).

V1 is a single-turn call with no custom tools — the model parses the raw
filename + current tags and returns a structured JSON payload. We don't
give it Discogs tool access yet; that's a later scope.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# Heuristic: does this PipelineItem look like a compilation-rip garbage-tagged
# file that still needs enrichment? The cheap guards keep Opus out of the loop
# when Discogs already produced clean metadata.
_CD_RE = re.compile(r'\bCD\s*\d+\b', re.IGNORECASE)
_TRACKNUM_RE = re.compile(r'\b\d{2}\b')


def looks_like_garbage(item) -> bool:
    """Return True if the item still has raw-file tags of the compilation-rip kind."""
    if item.metadata_source and item.metadata_source != 'file':
        # Discogs / MusicBrainz / manual already produced usable metadata.
        return False

    title = (item.title or '').strip()
    artist = (item.artist or '').strip()

    if not title and not artist:
        return False

    # Compilation CDs: 'CD 1 - 08 - Shatrax - ...' in the title.
    if _CD_RE.search(title):
        return True

    # Track title has 3+ ' - ' separators → probably 'Track# - Artist - Title - Remix'.
    if title.count(' - ') >= 3:
        return True

    # Artist field looks like a comp name ('VA - Foo' or contains 'Various').
    if artist.lower().startswith('va - ') or 'various' in artist.lower():
        return True

    # Artist literally equals the album/compilation name (common rip mistake).
    if item.album and artist.strip() == item.album.strip():
        return True

    return False


_SYSTEM_PROMPT = """You are a metadata parser for a DJ's music library pipeline.

You receive one track whose raw file tags were written by a Soulseek ripper
that tagged an entire compilation rather than the individual track. Your job:
parse the filename + current (garbage) tag fields and return the CORRECT
individual-track metadata as a single JSON object.

Rules:
- Return ONLY valid JSON. No prose. No code fences. No explanation.
- Output schema exactly:
  {
    "artist": "string — the single track's performing artist (not the compilation)",
    "title": "string — the actual song title, stripped of track numbers, CD markers, remixer names",
    "album": "string or null — the real album or compilation name the track belongs to",
    "label": "string or null — record label if you can infer it, else null",
    "year": "string or null — 4-digit year if present in filename/tags",
    "track_number": "string or null — if a leading NN is in the filename",
    "remix": "string or null — if the title contained a remix/edit credit, put it here",
    "confidence": "high | medium | low — your own read on how sure the parse is"
  }
- Do NOT invent data. If uncertain about a field, use null (or 'low' confidence).
- Preserve accents + casing from the source — don't title-case "dJ" into "Dj".
- Strip obvious filler: leading track numbers ('01. ', '08 - '), trailing bracket
  credits that belong in the `remix` field, duplicate artist-in-title.
"""


def _build_prompt(item) -> str:
    payload = {
        'filename': item.original_filename,
        'current_path_basename': os.path.basename(item.current_path or ''),
        'current_tags': {
            'artist': item.artist,
            'title': item.title,
            'album': item.album,
            'label': item.label,
            'year': item.year,
            'track_number': item.track_number,
            'genre': item.genre,
        },
        'metadata_source': item.metadata_source,
    }
    return (
        'Parse this track. Return only the JSON object.\n\n'
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


async def _query_async(prompt: str, timeout_seconds: int = 60) -> str | None:
    """Run one agent query. Returns the final assistant text or None on failure."""
    # Import inside the function so test environments without the SDK don't
    # blow up at import time.
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(
        max_turns=1,
        system_prompt=_SYSTEM_PROMPT,
        permission_mode='bypassPermissions',
        allowed_tools=[],
    )

    collected = []
    try:
        async with asyncio.timeout(timeout_seconds):
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collected.append(block.text)
                elif isinstance(message, ResultMessage):
                    # Final message — stream done.
                    break
    except asyncio.TimeoutError:
        logger.warning('agent_enrich: query timed out after %ss', timeout_seconds)
        return None
    except Exception as exc:  # SDK / CLI auth failures
        logger.error('agent_enrich: SDK error: %s', exc)
        return None

    return ''.join(collected).strip() or None


def _parse_json(raw: str) -> dict | None:
    """Tolerant JSON extract. Strips optional code fences + prose around the object."""
    if not raw:
        return None
    # Strip ``` fences if model ignored the instruction.
    stripped = re.sub(r'^```(?:json)?\s*', '', raw.strip())
    stripped = re.sub(r'\s*```$', '', stripped)
    # Grab the first {...} balanced chunk.
    m = re.search(r'\{.*\}', stripped, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        logger.warning('agent_enrich: JSON parse failed: %s — raw=%r', exc, raw[:200])
        return None


def enrich_pipeline_item(item) -> bool:
    """Run the agent, apply returned fields to the PipelineItem. Returns True on success.

    Only overwrites the fields the agent populated (non-null, non-empty).
    Sets metadata_source='manual' to indicate a post-file-tag enrichment ran.
    """
    if not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'):
        logger.info('agent_enrich: CLAUDE_CODE_OAUTH_TOKEN not set — skipping')
        return False

    prompt = _build_prompt(item)
    logger.info('agent_enrich: querying for item %s (title=%r)', item.id, item.title[:80])

    raw = asyncio.run(_query_async(prompt))
    if raw is None:
        return False

    parsed = _parse_json(raw)
    if parsed is None:
        logger.warning('agent_enrich: unparseable response for item %s: %r', item.id, raw[:200])
        return False

    # Apply fields. Skip empty / null / "unchanged" values.
    update_fields = []
    for field in ('artist', 'title', 'album', 'label', 'year', 'track_number'):
        val = parsed.get(field)
        if val is None or val == '':
            continue
        setattr(item, field, str(val).strip())
        update_fields.append(field)

    remix = parsed.get('remix')
    if remix:
        # Append remix credit to title if not already there.
        if remix.lower() not in (item.title or '').lower():
            item.title = f'{item.title} ({remix})'.strip()
            if 'title' not in update_fields:
                update_fields.append('title')

    if not update_fields:
        logger.info('agent_enrich: item %s — agent returned no usable fields', item.id)
        return False

    item.metadata_source = 'manual'
    update_fields.append('metadata_source')
    item.save(update_fields=update_fields + ['updated'])
    logger.info(
        'agent_enrich: item %s updated (confidence=%s, fields=%s)',
        item.id, parsed.get('confidence'), update_fields,
    )
    return True
