"""Claude vision album-cover identification.

Reuses the same `claude_agent_sdk` mechanism that `organize/services/agent_enrich.py`
uses for filename parsing — single-turn agent query authenticated via
`CLAUDE_CODE_OAUTH_TOKEN` (operator's Max subscription, no API key, no per-call cost).

This is the V2 replacement for `gemini.py`. Same return shape so `hybrid_search.py`
swaps with one import change. Empirical accuracy: 6/7 perfect ID + 1/7 album-only
on a mixed test set including textless iconic covers (Pink Floyd, Joy Division)
and obscure DJ 12"s (Theo Parrish, Moodymann). See test artifacts at
/tmp/cratemate-test/claude_v2_results.json.

The agent gets `Read` tool access and we hand it a temp file path so it loads
the image bytes itself — same pattern Claude Code uses for any image input.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import tempfile
from typing import Dict, Optional

from PIL import Image

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You identify album covers for a DJ's record-digging tool.

You will be given the path to an image file. Read it with the Read tool, look at
the artwork, and identify the release.

Return ONLY a JSON object with this exact shape:
{
  "artist": "string or null — the performing artist if you can identify it",
  "album": "string or null — the release title (album, EP, 12\\" title)",
  "label": "string or null — the record label, if visible/recognizable (e.g. 'Sound Signature', 'Strictly Rhythm', 'Warp')",
  "visible_text": "string — every legible word/character on the cover, in reading order, separated by ' | '",
  "genre": "string or null",
  "era": "string or null — year or decade if you can tell",
  "description": "string — short visual description that supports your ID",
  "confidence": "high | medium | low"
}

Critical rules for the artist/label distinction (a DJ's tool fails when these
are confused):
- Many obscure 12" / EP covers show the LABEL prominently (e.g. 'SOUND
  SIGNATURE', 'DW Art', 'STRICTLY RHYTHM') with the artist hidden in small
  print. Put the label in `label`, NOT in `artist`.
- If you cannot determine the actual performing artist, leave `artist` as
  null. Do NOT put the label name in `artist` as a guess.
- The downstream Discogs lookup will retry with `label`, `album`, and
  `visible_text` separately, so an honest `null` artist beats a wrong one.
- Examples:
   * Cover shows 'SOUND SIGNATURE' top, 'Parallel Dimensions' bottom →
     {artist: null, album: "Parallel Dimensions", label: "Sound Signature"}
   * Cover shows 'Daft Punk' (the chrome logo) → {artist: "Daft Punk",
     album: "Discovery", label: null}  (the logo IS the artist for this release)
   * Cover shows just a tracklist 'A1. Taipei Disco / B1. Body Movement' →
     {artist: null, album: "Taipei Disco", label: <if visible>}

Other rules:
- If the cover is iconic and you genuinely recognize it, give your best guess.
- Do NOT invent. If you cannot ID the release, set artist/album to null and
  describe what you see in `description` + `visible_text`.
- No prose outside the JSON. No code fences. No explanation.
- Preserve original casing, accents, and punctuation (e.g. 'D. W. Art' not 'DW Art').
"""


class ClaudeVisionCollector:
    """Drop-in replacement for `GeminiCollector`. Same return shape:

        {"success": bool, "result": {...}, "raw_response": str}
        OR {"success": False, "error": str}
    """

    def __init__(self):
        # In Docker we expect CLAUDE_CODE_OAUTH_TOKEN in env (set by compose).
        # On a dev workstation the `claude` CLI auths via Keychain so the env
        # var is optional. We treat ABSENCE of the env var as unconfigured for
        # the prod surface, matching agent_enrich.py's posture.
        token = os.getenv('CLAUDE_CODE_OAUTH_TOKEN', '').strip()
        self.configured = bool(token) and token != '__PENDING__'
        if not self.configured:
            logger.warning(
                'ClaudeVisionCollector: CLAUDE_CODE_OAUTH_TOKEN missing — '
                '/identify will return 503 until env is set.'
            )
        else:
            logger.info('ClaudeVisionCollector initialized (Max OAuth path)')

    async def identify_album(self, image: Image.Image, timeout_seconds: int = 90) -> Dict:
        """Identify album from a PIL Image. Mirrors `GeminiCollector.identify_album`."""
        if not self.configured:
            return {'success': False, 'error': 'CLAUDE_CODE_OAUTH_TOKEN not configured'}

        # Lazy import so test environments without the SDK don't blow up at import time
        # (mirrors the agent_enrich.py pattern).
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                TextBlock,
                query,
            )
        except ImportError as e:
            logger.error('claude-agent-sdk not installed: %s', e)
            return {'success': False, 'error': f'sdk import: {e}'}

        # Persist the image to a temp file the agent can Read. JPEG keeps the
        # payload small for the agent's image-token budget and matches what
        # Claude Code already optimizes for.
        suffix = '.jpg'
        with tempfile.NamedTemporaryFile(prefix='cratemate-', suffix=suffix, delete=False) as f:
            tmp_path = f.name
            try:
                safe_img = image.convert('RGB') if getattr(image, 'mode', '') in ('RGBA', 'P', 'L') else image
                # Cap dimensions so the model doesn't waste tokens on a 4K scan.
                max_dim = 1024
                if max(safe_img.size) > max_dim:
                    ratio = max_dim / max(safe_img.size)
                    new_size = (int(safe_img.size[0] * ratio), int(safe_img.size[1] * ratio))
                    safe_img = safe_img.resize(new_size, Image.LANCZOS)
                safe_img.save(f, format='JPEG', quality=88)
            except Exception as e:
                logger.error('claude_vision: image prep failed: %s', e)
                return {'success': False, 'error': f'image prep: {e}'}

        prompt = (
            f'Identify the album shown in the image at: {tmp_path}\n'
            f'Read the file with the Read tool, then return the JSON object as specified.'
        )

        options = ClaudeAgentOptions(
            max_turns=3,  # Read tool call + identification turn + safety margin
            system_prompt=_SYSTEM_PROMPT,
            allowed_tools=['Read'],
        )

        collected: list[str] = []
        try:
            async with asyncio.timeout(timeout_seconds):
                async for msg in query(prompt=prompt, options=options):
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                collected.append(block.text)
                    elif isinstance(msg, ResultMessage):
                        if msg.is_error:
                            logger.warning('claude_vision: SDK returned is_error=True')
                        break
        except asyncio.TimeoutError:
            logger.warning('claude_vision: query timed out after %ss', timeout_seconds)
            return {'success': False, 'error': f'timeout after {timeout_seconds}s'}
        except Exception as e:
            logger.error('claude_vision: SDK error: %s', e, exc_info=True)
            return {'success': False, 'error': f'sdk: {e}'}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        raw = '\n'.join(collected).strip()
        parsed = self._parse_response(raw)
        if parsed is None:
            logger.warning('claude_vision: unparseable response: %r', raw[:200])
            return {'success': False, 'error': 'unparseable response', 'raw_response': raw}

        # Normalize fields to the shape the rest of the pipeline expects.
        result = {
            'artist': self._clean(parsed.get('artist')),
            'album': self._clean(parsed.get('album')),
            'label': self._clean(parsed.get('label')),
            'visible_text': parsed.get('visible_text') or '',
            'genre': self._clean(parsed.get('genre')) or 'unknown',
            'era': self._clean(parsed.get('era')) or 'unknown',
            'description': parsed.get('description') or '',
            'confidence': (parsed.get('confidence') or 'low').lower(),
        }
        logger.info(
            'claude_vision: identified %r / %r (confidence=%s)',
            result['artist'], result['album'], result['confidence'],
        )
        return {'success': True, 'result': result, 'raw_response': raw}

    @staticmethod
    def _parse_response(text: str) -> Optional[dict]:
        if not text:
            return None
        # Strip ``` fences if model ignored the no-fences instruction.
        t = re.sub(r'^```(?:json)?\s*', '', text.strip())
        t = re.sub(r'\s*```$', '', t)
        m = re.search(r'\{.*\}', t, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            logger.warning('claude_vision: JSON parse failed: %s', e)
            return None

    @staticmethod
    def _clean(value: Optional[str]) -> Optional[str]:
        """Normalize 'unknown'/empty/n/a → None for downstream consumers."""
        if value is None:
            return None
        s = str(value).strip()
        if not s or s.lower() in {'unknown', 'n/a', 'na', 'not available', 'null', 'none'}:
            return None
        return s
