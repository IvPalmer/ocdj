"""Claude vision album-cover identification — V4 (simple).

V3 over-engineered: extraction-first prompts, evidence_quality gates, pHash
voting, fuzz floors. The user's complaint was correct — Gemini worked
because it just IDENTIFIED the cover the way a DJ would. This module is
now back to that.

Single call, image in, JSON out:
   { artist, album, label, confidence, evidence }

Auth: Claude Max via CLAUDE_CODE_OAUTH_TOKEN ($0/call). Direct image block
via streaming-input mode + setting_sources=[] so the Claude Code Read-tool
hook can't downscale our payload (V2's biggest mistake).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
from typing import Dict, Optional

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You identify album covers for a DJ.

Look at the image and tell me what release this is — the way you would
recognize a record while crate-digging. Trust your visual recognition.

Return ONLY a JSON object:

{
  "artist": "string or null — the performing artist",
  "album": "string or null — the release/album/EP title",
  "label": "string or null — record label if printed prominently",
  "confidence": "high | medium | low",
  "evidence": "string — short note on what you saw (visible text, iconic
    artwork, photo features) that supports the identification"
}

Rules:

- BE A DJ, not an OCR scanner. If you recognize the cover (iconic, well-known,
  or you can read the artist+title clearly), say so confidently.

- The biggest text on a record sleeve is sometimes the LABEL, not the
  artist. If only the label is printed prominently (e.g. 'Sound Signature',
  'Strictly Rhythm', 'Warp'), put it in `label`. Don't fake an artist.

- DECODE stylized text into the dictionary word it represents. Mixed
  Latin/katakana/cyrillic substitutions are common (ヤ for U, ナ for N,
  ル for L, Cyrillic Я for R, V for U). 'JヤSト WAナ FイEル' = 'Just Wanna
  Feel'. 'SPECTRVM' = 'Spectrum' or 'Spectral'. 'FLΞSH' = 'Flash' or
  'Flesh' (prefer dictionary + common record-title words).

- If you cannot identify the release, set artist+album to null. The user
  has a manual lookup fallback — empty is better than wrong.

- Preserve original casing, accents, punctuation in the names you DO emit
  (e.g. 'D. W. Art', 'NOMA', 'Sound Signature').

- NO PROSE outside the JSON. NO code fences. NO explanation.
"""


class ClaudeVisionCollector:
    """Drop-in replacement for the V3 collector. Same return shape:

        {"success": bool, "result": {...}, "raw_response": str}
        OR {"success": False, "error": str}
    """

    def __init__(self):
        token = os.getenv('CLAUDE_CODE_OAUTH_TOKEN', '').strip()
        self.configured = bool(token) and token != '__PENDING__'
        if not self.configured:
            logger.warning(
                'ClaudeVisionCollector: CLAUDE_CODE_OAUTH_TOKEN missing — '
                '/identify will return 503 until env is set.'
            )
        else:
            logger.info('ClaudeVisionCollector V4 initialized (DJ-style identification)')

    async def identify_album(self, image: Image.Image, timeout_seconds: int = 90) -> Dict:
        """Identify album from a PIL Image."""
        if not self.configured:
            return {'success': False, 'error': 'CLAUDE_CODE_OAUTH_TOKEN not configured'}

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

        # Image prep: EXIF rotate (phone landscape), max 1600 to preserve
        # cover-text legibility, JPEG q92. Skip the Read tool entirely so
        # Claude Code's image hook can't double-compress.
        try:
            img = ImageOps.exif_transpose(image)
            if img.mode in ('RGBA', 'P', 'L') or img.mode != 'RGB':
                img = img.convert('RGB')
            max_dim = 1600
            if max(img.size) > max_dim:
                ratio = max_dim / max(img.size)
                img = img.resize(
                    (int(img.size[0] * ratio), int(img.size[1] * ratio)),
                    Image.LANCZOS,
                )
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=92)
            image_b64 = base64.standard_b64encode(buf.getvalue()).decode('ascii')
            logger.info(
                'claude_vision: prepared image %dx%d, %d KB',
                img.size[0], img.size[1], len(image_b64) // 1024,
            )
        except Exception as e:
            logger.error('claude_vision: image prep failed: %s', e)
            return {'success': False, 'error': f'image prep: {e}'}

        async def _prompts():
            yield {
                'type': 'user',
                'message': {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': 'image/jpeg',
                                'data': image_b64,
                            },
                        },
                        {
                            'type': 'text',
                            'text': 'Identify this album cover. Return the JSON object.',
                        },
                    ],
                },
                'parent_tool_use_id': None,
                'session_id': 'cratemate-identify',
            }

        # CRITICAL: pin Opus explicitly. Without this the Claude Code default
        # routing was sending the bulk of token work to Haiku 4.5 — confirmed
        # via ResultMessage.model_usage on a probe call. Haiku's vision is
        # significantly weaker on stylized cover typography (the user's
        # "flesh" vs "flash" + "Spectraturm" vs "Spectral Turn" misreads
        # were the symptom). Opus is the strongest vision model available
        # via the Max subscription. Override-able via CRATEMATE_VISION_MODEL.
        model_id = os.getenv('CRATEMATE_VISION_MODEL', 'claude-opus-4-7')

        options = ClaudeAgentOptions(
            max_turns=1,
            model=model_id,
            system_prompt=_SYSTEM_PROMPT,
            allowed_tools=[],
            setting_sources=[],
        )

        collected: list[str] = []
        answering_model: Optional[str] = None
        try:
            async with asyncio.timeout(timeout_seconds):
                async for msg in query(prompt=_prompts(), options=options):
                    if isinstance(msg, AssistantMessage):
                        # AssistantMessage carries .model — the model that
                        # actually generated this turn. Log it so we can
                        # confirm Sonnet (not Haiku) is doing vision work.
                        m = getattr(msg, 'model', None)
                        if m and m != answering_model:
                            answering_model = m
                            logger.info('claude_vision: assistant model=%s', m)
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                collected.append(block.text)
                    elif isinstance(msg, ResultMessage):
                        # model_usage shows token totals per model — proves
                        # which one bore the load.
                        usage = getattr(msg, 'model_usage', None) or {}
                        if usage:
                            logger.info(
                                'claude_vision: model_usage=%s',
                                {k: {'in': v.get('inputTokens'), 'out': v.get('outputTokens')}
                                 for k, v in usage.items()},
                            )
                        if msg.is_error:
                            logger.warning('claude_vision: SDK is_error=True')
                        break
        except asyncio.TimeoutError:
            logger.warning('claude_vision: query timed out after %ss', timeout_seconds)
            return {'success': False, 'error': f'timeout after {timeout_seconds}s'}
        except Exception as e:
            logger.error('claude_vision: SDK error: %s', e, exc_info=True)
            return {'success': False, 'error': f'sdk: {e}'}

        raw = '\n'.join(collected).strip()
        parsed = self._parse_response(raw)
        if parsed is None:
            logger.warning('claude_vision: unparseable response: %r', raw[:200])
            return {'success': False, 'error': 'unparseable response', 'raw_response': raw}

        result = {
            'artist': self._clean(parsed.get('artist')),
            'album': self._clean(parsed.get('album')),
            'label': self._clean(parsed.get('label')),
            'confidence': (parsed.get('confidence') or 'low').lower(),
            'evidence': parsed.get('evidence') or '',
            # Legacy field retained for the views/serializer flatten — empty
            # because V4 prompt doesn't ask for separate visible_text.
            'visible_text': '',
            'description': parsed.get('evidence') or '',
            'genre': 'unknown',
            'era': 'unknown',
        }
        logger.info(
            'claude_vision: %r / %r (label=%r, conf=%s)',
            result['artist'], result['album'], result['label'], result['confidence'],
        )
        return {'success': True, 'result': result, 'raw_response': raw}

    @staticmethod
    def _parse_response(text: str) -> Optional[dict]:
        if not text:
            return None
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
        if value is None:
            return None
        s = str(value).strip()
        if not s or s.lower() in {'unknown', 'n/a', 'na', 'not available', 'null', 'none'}:
            return None
        return s
