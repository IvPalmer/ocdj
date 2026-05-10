"""Claude vision album-cover identification — V3 (direct image blocks).

V2 failed in production because images were being double-compressed before
Claude saw them: our 1024-JPEG-q88 resize, then the Claude Code Read-tool
hook resized again to 1000-JPEG-q70 (the parent CLI's CLAUDE_IMAGE_*
env). Real phone uploads of small DJ-record typography became blurry
mush; the model hallucinated text that wasn't there ("polo | polo i b"
on a sleeve with no such word, per user report).

V3 strategy (per codex review 2026-05-10):
 - Skip the Read tool entirely. Pass the image as a base64 image block
   directly in the user message via the SDK's streaming-input mode.
 - `setting_sources=[]` so user hooks (incl. the image-resize hook)
   don't touch our payload.
 - `allowed_tools=[]` — model has no tools, just text in / text out.
 - Higher fidelity: max 1600px, JPEG quality 92, with EXIF transpose so
   phone photos in landscape aren't analyzed sideways.
 - Extraction-first prompt: transcribe text first, ID only if evidence
   supports it. Aggressive `unknown` over hallucination.

Auth path is unchanged — still goes through CLAUDE_CODE_OAUTH_TOKEN
(Max subscription, $0/call). Same SDK that organize/services/agent_enrich.py
uses, just streaming-input mode this time.
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


_SYSTEM_PROMPT = """You are an OCR + visual-evidence extractor for a DJ's album-cover
identification tool.

You will receive ONE image of a record sleeve, CD case, or digital cover. Your job
is to extract the visible evidence carefully, then ONLY identify the release if
the evidence supports a confident match.

DO NOT GUESS FROM MEMORY. If the evidence is weak, say so.

Return ONLY a JSON object with this exact shape:

{
  "visible_text": "string — every legible word/character on the cover, in reading order, separated by ' | '. Be literal. Don't fix typos. Don't expand abbreviations. If you cannot read text confidently, set this to '' (empty), not your guess.",
  "description": "string — short factual visual description: dominant colors, layout, key visual element. Helps disambiguate if the text is sparse.",
  "is_iconic": "boolean — true ONLY if you immediately recognize this as a famous, widely-photographed cover (e.g. Dark Side of the Moon, Joy Division Unknown Pleasures, Daft Punk Discovery). False for everything else, especially obscure DJ 12\\"s.",
  "evidence_quality": "strong | weak | none — how confident are you that the visible_text + description uniquely identify a real release?",
  "artist": "string or null — the performing artist, if you can identify it from the visible text or iconic recognition. NEVER fill from the label name. NEVER invent.",
  "album": "string or null — the release/album/EP/12\\" title, from visible text or iconic recognition. NEVER invent.",
  "label": "string or null — the record label, ONLY if its name is clearly visible on the cover (e.g. 'Sound Signature', 'Strictly Rhythm', 'Warp Records'). NULL if the label is not printed.",
  "confidence": "high | medium | low — your overall confidence the artist/album are correct"
}

Critical rules:

1. EXTRACTION FIRST. Read the image carefully and transcribe every legible
   character into `visible_text` BEFORE deciding artist/album. Use ' | ' to
   separate distinct text blocks (logo / album title / track listing / tiny
   credits). Reading order: top-to-bottom, left-to-right.

2. NO HALLUCINATION. If you can't confidently read a word, omit it. Don't
   guess what blurry text "probably" says. An empty `visible_text` is far
   better than fabricated text — the downstream system has a manual fallback.

3. ARTIST vs LABEL distinction (DJ records constantly trip this up):
   - The biggest text on an obscure 12" is usually the LABEL (Sound Signature,
     DW Art, Strictly Rhythm), not the artist. Put the label in `label`,
     leave `artist` null unless the actual performer is also visible.
   - Iconic exception: if the cover IS the artist's logo (Daft Punk chrome
     graffiti, Aphex Twin black-A-on-white), that logotype IS the artist.
   - Examples:
     * Cover shows 'SOUND SIGNATURE' top + 'Parallel Dimensions' bottom →
       {artist: null, album: "Parallel Dimensions", label: "Sound Signature",
        evidence_quality: "strong"}
     * Cover shows just chrome 'daft punk' graffiti →
       {artist: "Daft Punk", album: "Discovery", label: null, is_iconic: true,
        evidence_quality: "strong"}
     * Cover is a record-store photo with no text →
       {artist: "DJ Shadow", album: "Endtroducing", label: null, is_iconic: true,
        evidence_quality: "strong" — only because it's iconic}
     * Cover is unfamiliar lavender collage with unclear text →
       {visible_text: "", description: "...", is_iconic: false,
        evidence_quality: "none", artist: null, album: null, label: null,
        confidence: "low"}

4. PRESERVE original casing, punctuation, accents (e.g. 'D. W. Art' not 'DW Art';
   'NOMA' not 'Noma'; 'Sound Signature' not 'sound signature').

5. NO PROSE outside the JSON. NO code fences. NO explanation.
"""


class ClaudeVisionCollector:
    """Drop-in replacement for `GeminiCollector`. Same return shape as V2:

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
            logger.info('ClaudeVisionCollector V3 initialized (direct image blocks)')

    async def identify_album(self, image: Image.Image, timeout_seconds: int = 90) -> Dict:
        """Identify album from a PIL Image.

        Sends the image as a base64 block in the streaming-input format so
        the Claude Code Read-tool image hook can't downscale our payload.
        """
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

        # Prep the image: EXIF rotate (phones store landscape with rotation
        # metadata), cap at 1600 (was 1024 — V2's biggest mistake), JPEG q92
        # (was 88). RGB conversion handles HEIC-derived RGBA / palette modes.
        try:
            img = ImageOps.exif_transpose(image)
            if img.mode in ('RGBA', 'P', 'L'):
                img = img.convert('RGB')
            elif img.mode != 'RGB':
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
                'claude_vision: prepared image %dx%d, %d KB base64',
                img.size[0], img.size[1], len(image_b64) // 1024,
            )
        except Exception as e:
            logger.error('claude_vision: image prep failed: %s', e)
            return {'success': False, 'error': f'image prep: {e}'}

        # Build the streaming user message. Anthropic's image content block
        # format — the CLI passes this through to the model unchanged when
        # we're in streaming-input mode and bypass user hooks via
        # setting_sources=[].
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
                            'text': (
                                'Extract the visible text and visual evidence from this '
                                'album cover, then identify the release ONLY if the '
                                'evidence supports it. Return the JSON object as '
                                'specified in the system prompt. No prose, no code fences.'
                            ),
                        },
                    ],
                },
                'parent_tool_use_id': None,
                'session_id': 'cratemate-identify',
            }

        options = ClaudeAgentOptions(
            max_turns=1,
            system_prompt=_SYSTEM_PROMPT,
            allowed_tools=[],          # text in, text out
            setting_sources=[],        # bypass user hooks (incl. image-resize hook)
        )

        collected: list[str] = []
        try:
            async with asyncio.timeout(timeout_seconds):
                async for msg in query(prompt=_prompts(), options=options):
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

        raw = '\n'.join(collected).strip()
        parsed = self._parse_response(raw)
        if parsed is None:
            logger.warning('claude_vision: unparseable response: %r', raw[:200])
            return {'success': False, 'error': 'unparseable response', 'raw_response': raw}

        # Normalize fields. New fields from V3 prompt: is_iconic, evidence_quality.
        result = {
            'artist': self._clean(parsed.get('artist')),
            'album': self._clean(parsed.get('album')),
            'label': self._clean(parsed.get('label')),
            'visible_text': parsed.get('visible_text') or '',
            'description': parsed.get('description') or '',
            'is_iconic': bool(parsed.get('is_iconic')),
            'evidence_quality': (parsed.get('evidence_quality') or 'none').lower(),
            'genre': self._clean(parsed.get('genre')) or 'unknown',
            'era': self._clean(parsed.get('era')) or 'unknown',
            'confidence': (parsed.get('confidence') or 'low').lower(),
        }
        logger.info(
            'claude_vision: %r / %r (label=%r, conf=%s, iconic=%s, evidence=%s)',
            result['artist'], result['album'], result['label'],
            result['confidence'], result['is_iconic'], result['evidence_quality'],
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
