"""OCDJ chat sidecar — FastAPI + Claude Agent SDK.

Runs on the HOST so the SDK can authenticate via the user's Claude CLI
(Max subscription). Reaches the dockerized backend at localhost:8002.

Frontend proxies `/sidecar/*` → here via the Vite dev server.
"""
import asyncio
import json
import os
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)
from claude_agent_sdk._errors import MessageParseError
from claude_agent_sdk._internal.message_parser import parse_message

from ocdj_tools import create_ocdj_mcp_server, ALLOWED_TOOLS

app = FastAPI(title='OCDJ Chat Sidecar')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5174', 'http://127.0.0.1:5174'],
    allow_methods=['*'],
    allow_headers=['*'],
)

_mcp = create_ocdj_mcp_server()
_session_id: Optional[str] = None


SYSTEM_PROMPT = """You are OCDJ's in-app assistant. OCDJ is a personal
music-pipeline tool (Django + React) for a DJ who uses Soulseek to source
tracks, recognizes mixtapes with ACRCloud/Shazam/TrackID, organizes files
through a 5-stage pipeline (downloaded → tagged → renamed → converted →
ready), and curates a growing wantlist.

Your job:
- Audit: find anomalies, stuck jobs, duplicates, orphans.
- Clean: propose and (with confirmation) execute tidy-up actions.
- Answer: resolve the user's questions about library state, what's in flight,
  what to do next. Be concrete — link to a specific item id when you can.
- Take action: call the mcp__ocdj-tools__* tools to read AND mutate state.
  Don't just talk; do the thing when asked.

Style:
- Terse. Bullet lists over paragraphs.
- Commit to a recommendation; then call the tool.
- If a tool fails, surface the error verbatim and suggest the fix.

You have read + write access to the full OCDJ API via tools. Use WebFetch
sparingly — most things you need are in the tool registry.
"""


class ChatRequest(BaseModel):
    message: str
    reset: bool = False


@app.get('/health')
async def health():
    return {'ok': True, 'sidecar': 'ocdj'}


@app.post('/chat')
async def chat(req: ChatRequest):
    async def stream():
        global _session_id

        if req.reset:
            _session_id = None

        options = ClaudeAgentOptions(
            max_turns=30,
            model='claude-opus-4-6',
            system_prompt=SYSTEM_PROMPT,
            include_partial_messages=True,
            mcp_servers={
                'ocdj-tools': {
                    'type': 'sdk',
                    'name': 'ocdj-tools',
                    'instance': _mcp,
                }
            },
            permission_mode='bypassPermissions',
            allowed_tools=ALLOWED_TOOLS + ['WebFetch', 'WebSearch'],
        )
        if _session_id:
            options.resume = _session_id

        full_response = ''
        last_sent_len = 0
        tool_calls_seen: list[str] = []

        try:
            client = ClaudeSDKClient(options)
            await client.connect()
            await client.query(req.message)

            session_id = None
            async for raw in client._query.receive_messages():
                try:
                    message = parse_message(raw)
                except (MessageParseError, Exception):
                    continue

                if isinstance(message, AssistantMessage):
                    content_blocks = getattr(message, 'content', [])
                    if not isinstance(content_blocks, list):
                        continue
                    text = ''
                    for block in content_blocks:
                        if isinstance(block, ToolUseBlock):
                            # Surface the tool name so the UI can chip-tag it.
                            # MCP tools arrive as `mcp__server__tool`; show the
                            # short form.
                            tname = block.name or ''
                            short = tname.split('__')[-1] if '__' in tname else tname
                            if short and short not in tool_calls_seen:
                                tool_calls_seen.append(short)
                                yield f"data: {json.dumps({'tool': short})}\n\n"
                        elif isinstance(block, TextBlock) and block.text:
                            text += block.text
                    if text and len(text) > last_sent_len:
                        full_response = text
                        yield f"data: {json.dumps({'content': text}, ensure_ascii=False)}\n\n"
                        last_sent_len = len(text)

                elif isinstance(message, ResultMessage):
                    session_id = getattr(message, 'session_id', None)
                    result_text = getattr(message, 'text', None)
                    if result_text and result_text != full_response:
                        full_response = result_text
                        yield f"data: {json.dumps({'content': result_text}, ensure_ascii=False)}\n\n"
                    break

            if session_id:
                _session_id = session_id

            await client.disconnect()
            yield f"data: {json.dumps({'done': True})}\n\n"

        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(stream(), media_type='text/event-stream')


@app.post('/reset')
async def reset():
    global _session_id
    _session_id = None
    return {'ok': True}
