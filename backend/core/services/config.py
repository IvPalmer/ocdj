"""Central configuration registry and resolver.

Single source of truth for every tunable in the app. Code should call
`get_config(key)` instead of reading `os.environ` or `django.conf.settings`
directly, so that a future frontend user can override any value without a
redeploy.

Resolution order:
    DB (core.Config) -> environment variable -> Django settings attr -> schema default
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from django.conf import settings


# ── Schema ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConfigSpec:
    key: str
    category: str
    type: str  # 'str' | 'int' | 'float' | 'bool' | 'json' | 'text' | 'path' | 'url'
    default: Any = ''
    is_secret: bool = False
    description: str = ''
    # When set, the resolver reads the legacy env var as an additional fallback.
    # Used so migrating from raw os.getenv() doesn't drop existing configurations.
    env_key: str | None = None
    # For bool/int fields: reject values that fail to cast
    choices: list | None = None


# Every tunable in OCDJ lives here. Adding a new one is the only place you
# should edit — `core.Config` rows, env vars, and settings all resolve through
# this registry.
SCHEMA: list[ConfigSpec] = [
    # ── paths ────────────────────────────────────────────────
    ConfigSpec('MUSIC_ROOT', 'paths', 'path', '/music',
               description='Top-level music folder (mounted into the backend container).'),
    ConfigSpec('SOULSEEK_DOWNLOAD_ROOT', 'paths', 'path', '/music/Electronic/ID3/soulseek',
               description='Where slskd drops downloads (pipeline stages live under this).'),
    ConfigSpec('TRAXDB_ROOT', 'paths', 'path', '/music/Electronic/ID3/traxdb',
               description='Where TraxDB blog downloads are archived by date.'),
    ConfigSpec('REVIEW_FOLDER', 'paths', 'path', '/music/Electronic/_Review',
               description="Review staging folder. Promote copies cleaned tracks here; "
                           "you review and drag to your library/iTunes manually."),

    # ── slskd ────────────────────────────────────────────────
    ConfigSpec('SLSKD_BASE_URL', 'slskd', 'url', 'http://slskd:5030',
               description='slskd HTTP API base URL.'),
    ConfigSpec('SLSKD_API_KEY', 'slskd', 'str', '', is_secret=True,
               description='slskd API key (set in slskd config).'),

    # ── spotify ──────────────────────────────────────────────
    ConfigSpec('SPOTIFY_CLIENT_ID', 'spotify', 'str', '', is_secret=True),
    ConfigSpec('SPOTIFY_CLIENT_SECRET', 'spotify', 'str', '', is_secret=True),
    ConfigSpec('SPOTIFY_REDIRECT_URI', 'spotify', 'url',
               'http://localhost:8002/api/wanted/import/spotify/callback/'),
    ConfigSpec('SPOTIFY_DEFAULT_PLAYLIST', 'spotify', 'url', ''),
    ConfigSpec('SPOTIFY_DEFAULT_PLAYLIST_NAME', 'spotify', 'str', ''),

    # ── youtube ──────────────────────────────────────────────
    ConfigSpec('YOUTUBE_API_KEY', 'youtube', 'str', '', is_secret=True),
    ConfigSpec('YOUTUBE_DEFAULT_PLAYLIST', 'youtube', 'url', ''),
    ConfigSpec('YOUTUBE_DEFAULT_PLAYLIST_NAME', 'youtube', 'str', ''),

    # ── soundcloud ───────────────────────────────────────────
    ConfigSpec('SC_CLIENT_ID', 'soundcloud', 'str', '', is_secret=True),
    ConfigSpec('SC_CLIENT_SECRET', 'soundcloud', 'str', '', is_secret=True),
    ConfigSpec('SC_DEFAULT_PLAYLIST', 'soundcloud', 'url', ''),
    ConfigSpec('SC_DEFAULT_PLAYLIST_NAME', 'soundcloud', 'str', ''),

    # ── discogs ──────────────────────────────────────────────
    ConfigSpec('DISCOGS_PERSONAL_TOKEN', 'discogs', 'str', '', is_secret=True),
    ConfigSpec('DISCOGS_USERNAME', 'discogs', 'str', ''),

    # ── acrcloud ─────────────────────────────────────────────
    ConfigSpec('ACRCLOUD_ACCESS_KEY', 'acrcloud', 'str', '', is_secret=True),
    ConfigSpec('ACRCLOUD_ACCESS_SECRET', 'acrcloud', 'str', '', is_secret=True),
    ConfigSpec('ACRCLOUD_HOST', 'acrcloud', 'str', 'identify-eu-west-1.acrcloud.com',
               description="Regional host. Change if your ACRCloud project is not eu-west-1."),
    ConfigSpec('ACRCLOUD_BEARER_TOKEN', 'acrcloud', 'str', '', is_secret=True,
               description='Bearer token for ACRCloud usage / billing API.'),

    # ── trackid.net ──────────────────────────────────────────
    ConfigSpec('TRACKID_TOKEN', 'trackid', 'str', '', is_secret=True),
    ConfigSpec('TRACKID_CF_CLEARANCE', 'trackid', 'str', '', is_secret=True,
               description='Cloudflare clearance cookie. Stale values cause silent zero-results.'),

    # ── acoustid ─────────────────────────────────────────────
    ConfigSpec('ACOUSTID_API_KEY', 'acoustid', 'str', '', is_secret=True,
               description='Currently unused; reserved for future download verification.'),

    # ── recognize tuning ─────────────────────────────────────
    ConfigSpec('RECOGNIZE_SEGMENT_DURATION', 'recognize', 'int', 12,
               description='Seconds per audio segment for fingerprinting.'),
    ConfigSpec('RECOGNIZE_SEGMENT_STEP', 'recognize', 'int', 10,
               description='Seconds between segment starts (Shazam-only fallback mode).'),
    ConfigSpec('RECOGNIZE_ACRCLOUD_STEP', 'recognize', 'int', 20,
               description='Seconds between ACRCloud segments. Denser adds noise.'),
    ConfigSpec('RECOGNIZE_GAP_THRESHOLD', 'recognize', 'int', 20,
               description='Minimum unidentified gap (s) before triggering Shazam gap fill.'),
    ConfigSpec('RECOGNIZE_GAP_SEGMENT_DURATION', 'recognize', 'int', 15),
    ConfigSpec('RECOGNIZE_GAP_SEGMENT_STEP', 'recognize', 'int', 8),
    ConfigSpec('RECOGNIZE_MAX_GAP_SEGMENTS', 'recognize', 'int', 500,
               description='Cap on Shazam gap-fill segments (~17 min at step=8).'),
    ConfigSpec('RECOGNIZE_PROXIMITY_WINDOW', 'recognize', 'int', 120,
               description='Max gap (s) between segments of the same track to merge them.'),
    ConfigSpec('RECOGNIZE_CONFLICT_WINDOW', 'recognize', 'int', 30,
               description='Window (s) in which only the best of competing matches is kept. Lower allows mashups.'),
    ConfigSpec('RECOGNIZE_ACR_LOW_SCORE_MIN_SEGMENTS', 'recognize', 'int', 3,
               description='Accept ACRCloud score >=25 when matched by at least this many segments.'),

    # ── organize ─────────────────────────────────────────────
    ConfigSpec('ORGANIZE_RENAME_TEMPLATE', 'organize', 'str',
               '{artist} - {title}',
               description='Filename template. Placeholders: {artist}, {title}, {label}, {catalog}, {year}. '
                           'Artist/title are auto-cleaned of catalog brackets, URL stamps and leading track numbers.'),
    ConfigSpec('ORGANIZE_CONVERSION_RULES', 'organize', 'text',
               'wav -> aiff\nflac -> aiff\nmp3>=320k -> keep',
               description='DSL rules, one per line. Left side: format[>=bitrate]. Right: target or keep.'),

    # ── traxdb ───────────────────────────────────────────────
    ConfigSpec('TRAXDB_START_URL', 'traxdb', 'url', '',
               description='URL the TraxDB scraper starts from when looking for new lists.'),
    ConfigSpec('PIXELDRAIN_API_KEY', 'traxdb', 'str', '', is_secret=True,
               description='Pixeldrain API key (boosts download throughput).'),
    ConfigSpec('TRAXDB_COOKIES', 'traxdb', 'str', '',
               description='Path inside the container to a Netscape-format cookies.txt for blog auth.'),

    # ── automation ───────────────────────────────────────────
    ConfigSpec('AUTOMATION_ENABLED', 'automation', 'bool', False,
               description='Master switch for auto-pipeline cycles.'),
    ConfigSpec('AUTOMATION_AUTO_SEARCH', 'automation', 'bool', False),
    ConfigSpec('AUTOMATION_AUTO_DOWNLOAD', 'automation', 'bool', False),
    ConfigSpec('AUTOMATION_AUTO_ORGANIZE', 'automation', 'bool', False),
    ConfigSpec('AUTOMATION_CONFIDENCE_THRESHOLD', 'automation', 'int', 85,
               description='Minimum match score (0-100) before auto-download fires.'),
    ConfigSpec('AUTOMATION_POLL_INTERVAL_MINUTES', 'automation', 'int', 0,
               description='How often the worker re-runs the automation cycle. 0 = manual only.'),
]


_BY_KEY: dict[str, ConfigSpec] = {s.key: s for s in SCHEMA}


# ── Public API ────────────────────────────────────────────────────────

def get_spec(key: str) -> ConfigSpec | None:
    return _BY_KEY.get(key)


def list_specs(category: str | None = None) -> list[ConfigSpec]:
    if category is None:
        return list(SCHEMA)
    return [s for s in SCHEMA if s.category == category]


def categories() -> list[str]:
    seen = []
    for s in SCHEMA:
        if s.category not in seen:
            seen.append(s.category)
    return seen


def get_config(key: str, default: Any = None, cast: Callable[[str], Any] | None = None) -> Any:
    """Resolve a config value.

    Order: DB (core.Config) -> env var -> Django settings attr -> schema default -> `default`.

    Values in DB / env / settings are always strings and get cast by the
    schema type (or by an explicit `cast` override). If the lookup fails
    entirely, returns `default` (or the schema default when `default` is None).
    """
    spec = _BY_KEY.get(key)

    raw = _raw_lookup(key, spec)
    if raw is None or raw == '':
        if default is not None:
            return default
        return spec.default if spec else ''

    if cast is not None:
        try:
            return cast(raw)
        except (TypeError, ValueError):
            return default if default is not None else (spec.default if spec else raw)

    if spec is None:
        return raw
    return _cast(raw, spec.type, fallback=spec.default)


def set_config(key: str, value: Any) -> None:
    """Write a value to the DB config store. Accepts any type; stores as str."""
    # Local import to avoid circular at module load.
    from core.models import Config
    if value is None:
        value = ''
    if isinstance(value, bool):
        value = '1' if value else '0'
    Config.objects.update_or_create(key=key, defaults={'value': str(value)})


def source_of(key: str) -> str:
    """Return where the current value comes from: 'db' | 'env' | 'settings' | 'default' | 'unset'."""
    from core.models import Config
    if Config.objects.filter(key=key).exists():
        return 'db'
    spec = _BY_KEY.get(key)
    env_keys = [key]
    if spec and spec.env_key:
        env_keys.append(spec.env_key)
    if any(os.environ.get(k) for k in env_keys):
        return 'env'
    if getattr(settings, key, None):
        return 'settings'
    if spec and spec.default not in ('', None, 0, False):
        return 'default'
    return 'unset'


def mask_value(value: str, spec: ConfigSpec | None) -> str:
    """Return a display-safe representation for the given spec."""
    if not spec or not spec.is_secret:
        return value or ''
    if not value:
        return ''
    if len(value) <= 10:
        return '*' * len(value)
    return value[:4] + '*' * (len(value) - 8) + value[-4:]


# ── Internals ─────────────────────────────────────────────────────────

def _raw_lookup(key: str, spec: ConfigSpec | None) -> str | None:
    from core.models import Config
    try:
        row = Config.objects.get(key=key)
        return row.value
    except Config.DoesNotExist:
        pass
    env_keys = [key]
    if spec and spec.env_key:
        env_keys.append(spec.env_key)
    for k in env_keys:
        v = os.environ.get(k)
        if v:
            return v
    v = getattr(settings, key, None)
    if v:
        return str(v)
    return None


def _cast(raw: str, type_: str, fallback: Any = None) -> Any:
    try:
        if type_ == 'int':
            return int(raw)
        if type_ == 'float':
            return float(raw)
        if type_ == 'bool':
            return str(raw).lower() in ('1', 'true', 'yes', 'on')
        if type_ == 'json':
            import json
            return json.loads(raw)
        return raw
    except (TypeError, ValueError):
        return fallback
