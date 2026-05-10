"""Cratemate Django app — album-cover identification + multi-platform metadata.

Ported from IvPalmer/crate-mate (FastAPI + Streamlit) per
docs/plans/INGEST-CRATE-MATE-AS-MODULE.md. Image-recognition sibling of the
audio-focused `recognize` app — different modality, different external services,
deliberately not merged.
"""
import logging
import os

from django.apps import AppConfig

logger = logging.getLogger(__name__)


REQUIRED_ENV_VARS = (
    # Recognition backend — Claude vision via the operator's Max subscription.
    # Same token agent_enrich.py already uses for filename parsing.
    'CLAUDE_CODE_OAUTH_TOKEN',
    # Enrichment backends — required for the Discogs/Spotify/YouTube/Bandcamp
    # cross-platform link payload that follows recognition.
    'CRATEMATE_DISCOGS_TOKEN',
    'CRATEMATE_SPOTIFY_CLIENT_ID',
    'CRATEMATE_SPOTIFY_CLIENT_SECRET',
)


class CratemateConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cratemate'
    verbose_name = 'Crate-Mate (album cover ID)'

    def ready(self):
        # Boot-time guard: warn on missing/placeholder env vars so the module
        # degrades gracefully (HTTP 503 from views) instead of crashing the
        # whole Django process. Smoke testing with placeholder envs should
        # render the upload UI and return a clean "credentials not configured"
        # error from the identify endpoint.
        missing = []
        placeholder = []
        for var in REQUIRED_ENV_VARS:
            val = os.getenv(var, '')
            if not val:
                missing.append(var)
            elif val == '__PENDING__':
                placeholder.append(var)

        if missing:
            logger.warning(
                '[cratemate] Missing env vars: %s. Identify endpoint will return 503 '
                'until these are set in ~/.secrets/ocdj-cratemate.env (Mac) or Dokploy env (VPS).',
                ', '.join(missing),
            )
        if placeholder:
            logger.warning(
                '[cratemate] Placeholder env vars (__PENDING__): %s. Phase 1 of the '
                'absorption plan rotates these — see docs/plans/INGEST-CRATE-MATE-AS-MODULE.md.',
                ', '.join(placeholder),
            )
