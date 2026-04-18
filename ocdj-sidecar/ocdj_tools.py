"""OCDJ MCP tools — exposes the backend REST API to the agent.

Every tool returns either structured JSON text or a short human-readable
summary. All HTTP calls hit `OCDJ_API` which is the Django backend (port
8002). Runs on the host; reaches the Dockerized backend via localhost.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server import Server
from mcp.types import Tool, TextContent

OCDJ_API = os.environ.get('OCDJ_API', 'http://localhost:8002/api')
_TIMEOUT = 30


def _client() -> httpx.Client:
    return httpx.Client(base_url=OCDJ_API, timeout=_TIMEOUT)


def _text(payload: Any) -> list[TextContent]:
    if isinstance(payload, str):
        return [TextContent(type='text', text=payload)]
    return [TextContent(type='text', text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _safe(fn, *args, **kwargs) -> list[TextContent]:
    try:
        return _text(fn(*args, **kwargs))
    except httpx.HTTPError as e:
        return _text(f'HTTP error: {e}')
    except Exception as e:
        return _text(f'error: {type(e).__name__}: {e}')


# ── Tool implementations ──────────────────────────────────────────────

def _get_stats() -> dict:
    with _client() as c:
        health = c.get('/core/health/').json()
        stats = c.get('/core/stats/').json()
        lib = c.get('/library/stats/').json()
    return {'health': health, 'queue_stats': stats, 'library_stats': lib}


def _list_wanted(status: str | None = None, search: str | None = None,
                 limit: int = 50) -> dict:
    params = {}
    if status:
        params['status'] = status
    if search:
        params['search'] = search
    with _client() as c:
        data = c.get('/wanted/items/', params=params).json()
    items = data.get('results', data)[:limit] if isinstance(data, dict) else data[:limit]
    return {'count': data.get('count', len(items)) if isinstance(data, dict) else len(items),
            'items': [
                {
                    'id': i['id'], 'artist': i['artist'], 'title': i['title'],
                    'status': i.get('status'), 'source': i.get('source_name', ''),
                    'score': i.get('score'),
                }
                for i in items
            ]}


def _update_wanted(item_id: int, **fields) -> dict:
    with _client() as c:
        r = c.patch(f'/wanted/items/{item_id}/', json=fields)
        r.raise_for_status()
        return r.json()


def _delete_wanted(item_id: int) -> dict:
    with _client() as c:
        r = c.delete(f'/wanted/items/{item_id}/')
        return {'ok': r.status_code in (200, 204), 'status': r.status_code}


def _list_library(search: str | None = None, format: str | None = None,
                  limit: int = 50) -> dict:
    params = {}
    if search:
        params['search'] = search
    if format:
        params['format'] = format
    with _client() as c:
        data = c.get('/library/tracks/', params=params).json()
    results = data.get('results', [])[:limit]
    return {'count': data.get('count', 0),
            'tracks': [
                {
                    'id': t['id'], 'artist': t.get('artist'), 'title': t.get('title'),
                    'album': t.get('album'), 'format': t.get('format'),
                    'bitrate': t.get('bitrate'),
                    'duration_seconds': t.get('duration_seconds'),
                    'missing': t.get('missing'),
                }
                for t in results
            ]}


def _list_recognize_jobs(status: str | None = None, limit: int = 30) -> dict:
    params = {}
    if status:
        params['status'] = status
    with _client() as c:
        data = c.get('/recognize/jobs/', params=params).json()
    results = data.get('results', data) if isinstance(data, dict) else data
    return {'count': len(results),
            'jobs': [
                {
                    'id': j['id'], 'title': j.get('title'), 'status': j.get('status'),
                    'tracks_found': j.get('tracks_found'),
                    'segments_done': j.get('segments_done'),
                    'segments_total': j.get('segments_total'),
                    'updated': j.get('updated'),
                }
                for j in results[:limit]
            ]}


def _list_pipeline_items(stage: str | None = None, limit: int = 100) -> dict:
    params = {}
    if stage:
        params['stage'] = stage
    with _client() as c:
        data = c.get('/organize/pipeline/', params=params).json()
    items = data.get('results', data) if isinstance(data, dict) else data
    return {'count': len(items),
            'items': [
                {
                    'id': i['id'], 'stage': i.get('stage'),
                    'original_filename': i.get('original_filename'),
                    'artist': i.get('artist'), 'title': i.get('title'),
                    'error': i.get('error_message'),
                }
                for i in items[:limit]
            ]}


def _trigger_scan_downloads() -> dict:
    with _client() as c:
        r = c.post('/organize/pipeline/scan/')
        return r.json()


def _trigger_library_scan() -> dict:
    with _client() as c:
        r = c.post('/library/scan/sync/')
        return r.json()


def _trigger_audit(apply: bool = False, reclassify: list | None = None) -> dict:
    """Run the audit_music_root management command via HTTP wrapper."""
    payload = {'apply': apply}
    if reclassify:
        payload['reclassify'] = reclassify
    with _client() as c:
        r = c.post('/core/audit-music-root/', json=payload, timeout=120)
        return r.json()


def _promote_library_track(track_id: int) -> dict:
    with _client() as c:
        r = c.post(f'/library/tracks/{track_id}/promote/')
        return r.json()


def _get_config(key: str) -> dict:
    with _client() as c:
        data = c.get('/core/config/').json()
    if key not in data:
        return {'error': f'unknown config key: {key}'}
    return data[key]


def _find_stuck_jobs(max_age_hours: int = 6) -> dict:
    """Find recognize jobs stuck in-flight (status=recognizing OR downloading)
    AND idle longer than max_age_hours. Backend doesn't filter by status on
    the list endpoint in all cases, so filter client-side explicitly."""
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    with _client() as c:
        data = c.get('/recognize/jobs/', params={'page_size': 500}).json()
    results = data.get('results', data) if isinstance(data, dict) else data
    stuck = []
    for j in results:
        if j.get('status') not in ('recognizing', 'downloading'):
            continue
        updated = j.get('updated') or ''
        try:
            t = datetime.fromisoformat(updated.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            continue
        if t < cutoff:
            stuck.append({
                'id': j['id'], 'status': j['status'],
                'title': j.get('title'), 'updated': updated,
                'segments_done': j.get('segments_done'),
                'segments_total': j.get('segments_total'),
            })
    return {'cutoff_hours': max_age_hours,
            'stuck_count': len(stuck),
            'jobs': stuck}


def _find_duplicates_wanted(similarity: float = 0.88) -> dict:
    """Fuzzy duplicate detection across the wantlist."""
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return {'error': 'rapidfuzz not installed in sidecar venv'}
    with _client() as c:
        # Pull the full list — for personal-scale this is fine.
        data = c.get('/wanted/items/', params={'page_size': 5000}).json()
    items = data.get('results', [])
    seen_keys = {}
    groups = []
    for a in items:
        key_a = f"{a.get('artist', '').lower()}||{a.get('title', '').lower()}"
        matched = False
        for rep_key, idx in seen_keys.items():
            score = fuzz.token_set_ratio(key_a, rep_key) / 100.0
            if score >= similarity:
                groups[idx].append({'id': a['id'], 'artist': a.get('artist'),
                                    'title': a.get('title'), 'score': round(score, 3)})
                matched = True
                break
        if not matched:
            seen_keys[key_a] = len(groups)
            groups.append([{'id': a['id'], 'artist': a.get('artist'),
                            'title': a.get('title'), 'score': 1.0}])
    dup_groups = [g for g in groups if len(g) > 1]
    return {'similarity_threshold': similarity,
            'duplicate_group_count': len(dup_groups),
            'groups': dup_groups[:30]}


# ── MCP server ────────────────────────────────────────────────────────

def create_ocdj_mcp_server() -> Server:
    server = Server('ocdj-tools')

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name='get_stats',
                 description='Overall dashboard: pipeline counts, library counts, service health.',
                 inputSchema={'type': 'object', 'properties': {}}),
            Tool(name='list_wanted',
                 description='List wantlist items. Filter by status (pending/searching/downloaded/failed) and search.',
                 inputSchema={'type': 'object', 'properties': {
                     'status': {'type': 'string'},
                     'search': {'type': 'string'},
                     'limit': {'type': 'integer', 'default': 50},
                 }}),
            Tool(name='update_wanted',
                 description='Update a wanted item. Body keys: artist, title, release_name, label, catalog_number, status, notes.',
                 inputSchema={'type': 'object', 'properties': {
                     'item_id': {'type': 'integer'},
                     'fields': {'type': 'object'},
                 }, 'required': ['item_id', 'fields']}),
            Tool(name='delete_wanted',
                 description='Delete a wanted item by id.',
                 inputSchema={'type': 'object', 'properties': {
                     'item_id': {'type': 'integer'},
                 }, 'required': ['item_id']}),
            Tool(name='list_library',
                 description='Browse the ready-tracks library. Filter by search string, format (mp3/flac/aiff/wav).',
                 inputSchema={'type': 'object', 'properties': {
                     'search': {'type': 'string'},
                     'format': {'type': 'string'},
                     'limit': {'type': 'integer', 'default': 50},
                 }}),
            Tool(name='list_recognize_jobs',
                 description='List mix recognition jobs. Filter by status (pending/downloading/recognizing/completed/failed).',
                 inputSchema={'type': 'object', 'properties': {
                     'status': {'type': 'string'},
                     'limit': {'type': 'integer', 'default': 30},
                 }}),
            Tool(name='list_pipeline_items',
                 description='List Organize pipeline items. Filter by stage (downloaded/tagged/renamed/converted/ready/failed).',
                 inputSchema={'type': 'object', 'properties': {
                     'stage': {'type': 'string'},
                     'limit': {'type': 'integer', 'default': 100},
                 }}),
            Tool(name='scan_downloads',
                 description='Trigger Organize to scan 01_downloaded/ (+ orphans in _to_triage) and create PipelineItems for untracked files.',
                 inputSchema={'type': 'object', 'properties': {}}),
            Tool(name='scan_library',
                 description='Rescan the 05_ready folder and populate the library DB.',
                 inputSchema={'type': 'object', 'properties': {}}),
            Tool(name='audit_music_root',
                 description='Run the ID3 folder audit. Dry-run by default. Pass apply=true to execute, or reclassify=["name"] to sweep user-content folders.',
                 inputSchema={'type': 'object', 'properties': {
                     'apply': {'type': 'boolean', 'default': False},
                     'reclassify': {'type': 'array', 'items': {'type': 'string'}},
                 }}),
            Tool(name='promote_track',
                 description='Copy a library track from 05_ready into the REVIEW_FOLDER for manual handling.',
                 inputSchema={'type': 'object', 'properties': {
                     'track_id': {'type': 'integer'},
                 }, 'required': ['track_id']}),
            Tool(name='get_config',
                 description='Read a single config key (current value + source).',
                 inputSchema={'type': 'object', 'properties': {
                     'key': {'type': 'string'},
                 }, 'required': ['key']}),
            Tool(name='find_stuck_jobs',
                 description='Find recognize jobs stuck in `recognizing` status longer than max_age_hours.',
                 inputSchema={'type': 'object', 'properties': {
                     'max_age_hours': {'type': 'integer', 'default': 6},
                 }}),
            Tool(name='find_duplicates_wanted',
                 description='Fuzzy-match duplicates in the wanted list. Returns groups of likely duplicates.',
                 inputSchema={'type': 'object', 'properties': {
                     'similarity': {'type': 'number', 'default': 0.88},
                 }}),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None = None) -> list[TextContent]:
        arguments = arguments or {}
        if name == 'get_stats':
            return _safe(_get_stats)
        if name == 'list_wanted':
            return _safe(_list_wanted, **arguments)
        if name == 'update_wanted':
            return _safe(_update_wanted, arguments['item_id'], **arguments.get('fields', {}))
        if name == 'delete_wanted':
            return _safe(_delete_wanted, arguments['item_id'])
        if name == 'list_library':
            return _safe(_list_library, **arguments)
        if name == 'list_recognize_jobs':
            return _safe(_list_recognize_jobs, **arguments)
        if name == 'list_pipeline_items':
            return _safe(_list_pipeline_items, **arguments)
        if name == 'scan_downloads':
            return _safe(_trigger_scan_downloads)
        if name == 'scan_library':
            return _safe(_trigger_library_scan)
        if name == 'audit_music_root':
            return _safe(_trigger_audit,
                         apply=arguments.get('apply', False),
                         reclassify=arguments.get('reclassify'))
        if name == 'promote_track':
            return _safe(_promote_library_track, arguments['track_id'])
        if name == 'get_config':
            return _safe(_get_config, arguments['key'])
        if name == 'find_stuck_jobs':
            return _safe(_find_stuck_jobs, **arguments)
        if name == 'find_duplicates_wanted':
            return _safe(_find_duplicates_wanted, **arguments)
        return _text(f'unknown tool: {name}')

    return server


ALLOWED_TOOLS = [
    f'mcp__ocdj-tools__{n}' for n in [
        'get_stats', 'list_wanted', 'update_wanted', 'delete_wanted',
        'list_library', 'list_recognize_jobs', 'list_pipeline_items',
        'scan_downloads', 'scan_library', 'audit_music_root',
        'promote_track', 'get_config',
        'find_stuck_jobs', 'find_duplicates_wanted',
    ]
]
