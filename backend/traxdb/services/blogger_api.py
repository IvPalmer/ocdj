"""Headless Blogger API v3 fetch path for TraxDB sync.

Replaces the fragile Google-cookie scraping with the official Blogger API and a
durable OAuth refresh-token grant. Access tokens are minted per-run from the
three ``BLOGGER_*`` config values; no Google SDK is needed (plain ``requests``).

Flow: refresh_token grant -> access token -> resolve the private blog via
``blogs/byurl`` -> paginate ``posts.list`` (view=READER, fetchBodies=true) ->
extract pixeldrain links from each post body with the same parser the cookie
path uses.

If the refresh token is ever revoked/expired, ``_mint_access_token`` raises
``BloggerAuthError`` pointing at the re-bootstrap runbook
(``tools/traxdb_sync/blogger_oauth_bootstrap.py``).
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

from core.services.config import get_config

from .scraper import TraxDBLink, parse_traxdb_links_from_html

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
BLOGGER_BASE = "https://www.googleapis.com/blogger/v3"
POSTS_FIELDS = "items(id,url,published,updated,title,content),nextPageToken"

# Backoff for transient failures (HTTP 429 / 5xx, timeouts, connection
# errors): up to 3 tries, 2/4/8s.
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_TRIES = 3


class BloggerAuthError(RuntimeError):
    """Raised when the OAuth refresh token is rejected (invalid_grant)."""


class BloggerAPIError(RuntimeError):
    """Raised for non-retryable Blogger API failures."""


def _mint_access_token() -> str:
    """Exchange the stored refresh token for a short-lived access token."""
    client_id = get_config('BLOGGER_CLIENT_ID')
    client_secret = get_config('BLOGGER_CLIENT_SECRET')
    refresh_token = get_config('BLOGGER_REFRESH_TOKEN')

    missing = [
        name for name, val in (
            ('BLOGGER_CLIENT_ID', client_id),
            ('BLOGGER_CLIENT_SECRET', client_secret),
            ('BLOGGER_REFRESH_TOKEN', refresh_token),
        ) if not val
    ]
    if missing:
        raise BloggerAuthError(
            "Missing Blogger OAuth config: " + ", ".join(missing) + ". "
            "Re-run the bootstrap: tools/traxdb_sync/blogger_oauth_bootstrap.py"
        )

    resp = requests.post(
        TOKEN_URL,
        data={
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        },
        timeout=30,
    )

    if resp.status_code == 400:
        # Google returns 400 {"error":"invalid_grant",...} when the refresh
        # token has been revoked / expired / never valid.
        try:
            err = resp.json().get('error')
        except ValueError:
            err = None
        if err == 'invalid_grant':
            raise BloggerAuthError(
                "Blogger refresh token rejected (invalid_grant) — it was "
                "revoked or expired. Re-run the bootstrap on the Mac to mint a "
                "new one: tools/traxdb_sync/blogger_oauth_bootstrap.py, then "
                "update BLOGGER_REFRESH_TOKEN in the config store."
            )

    if resp.status_code != 200:
        raise BloggerAPIError(
            f"Token endpoint returned {resp.status_code}: {resp.text[:300]}"
        )

    token = resp.json().get('access_token')
    if not token:
        raise BloggerAPIError("Token response had no access_token")
    return token


def _api_get(url: str, access_token: str, params: Optional[dict] = None) -> dict:
    """GET a Blogger API endpoint with bearer auth + backoff on 429/5xx and
    transient network errors (timeouts / connection resets)."""
    headers = {'Authorization': f'Bearer {access_token}'}
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_TRIES):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=60)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt < _MAX_TRIES - 1:
                sleep_s = 2 ** (attempt + 1)  # 2, 4, 8
                logger.warning(
                    "Blogger API network error (%s): %r; retry %d/%d in %ds",
                    url, e, attempt + 1, _MAX_TRIES - 1, sleep_s,
                )
                time.sleep(sleep_s)
                continue
            raise
        if resp.status_code in _RETRY_STATUSES:
            last_exc = BloggerAPIError(
                f"{url} returned {resp.status_code}: {resp.text[:200]}"
            )
            if attempt < _MAX_TRIES - 1:
                sleep_s = 2 ** (attempt + 1)  # 2, 4, 8
                logger.warning(
                    "Blogger API %s (%s); retry %d/%d in %ds",
                    resp.status_code, url, attempt + 1, _MAX_TRIES - 1, sleep_s,
                )
                time.sleep(sleep_s)
                continue
            raise last_exc
        if resp.status_code != 200:
            raise BloggerAPIError(
                f"{url} returned {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()
    # Unreachable, but keep the type-checker/readers happy.
    raise last_exc or BloggerAPIError(f"{url} failed")


def _resolve_blog_id(start_url: str, access_token: str) -> str:
    """Resolve a blog's numeric id from its public URL via blogs/byurl.

    ``start_url`` may carry a path or query (e.g. a /search?... page); byurl
    wants the blog root, so normalize to ``scheme://netloc/``.
    """
    parsed = urlparse(start_url)
    if not parsed.scheme or not parsed.netloc:
        raise BloggerAPIError(f"TRAXDB_START_URL is not a valid URL: {start_url!r}")
    url = f"{parsed.scheme}://{parsed.netloc}/"
    data = _api_get(
        f"{BLOGGER_BASE}/blogs/byurl", access_token, params={'url': url}
    )
    blog_id = data.get('id')
    if not blog_id:
        raise BloggerAPIError(f"blogs/byurl returned no id for {url}: {data}")
    logger.info(
        "Resolved TraxDB blog %r (id=%s, %s posts)",
        data.get('name'), blog_id, data.get('posts', {}).get('totalItems'),
    )
    return blog_id


def iter_blog_links(
    start_url: str,
    *,
    max_pages: int = 50,
    stop_at_or_before_date: Optional[str] = None,
) -> List[TraxDBLink]:
    """Fetch pixeldrain list links from the blog via the Blogger API.

    Mints an access token, resolves the blog, and paginates ``posts.list``.
    Each post's ``content`` HTML runs through ``parse_traxdb_links_from_html``
    with the post's ``published`` date as the fallback date.

    ``max_pages`` bounds the number of API pages fetched (25 posts each).
    ``stop_at_or_before_date`` stops paging once a post's date is <= it (posts
    come newest-first), mirroring the cookie path's early-stop.
    """
    access_token = _mint_access_token()
    blog_id = _resolve_blog_id(start_url, access_token)

    found: Dict[str, TraxDBLink] = {}
    page_token: Optional[str] = None
    pages_done = 0
    stop = False

    for _ in range(max_pages):
        params = {
            'maxResults': 25,
            'fetchBodies': 'true',
            'view': 'READER',
            # Explicit newest-first ordering so the stop_at_or_before_date
            # cutoff assumption is guaranteed, not just the API default.
            'orderBy': 'published',
            'fields': POSTS_FIELDS,
        }
        if page_token:
            params['pageToken'] = page_token

        data = _api_get(f"{BLOGGER_BASE}/blogs/{blog_id}/posts", access_token, params=params)
        pages_done += 1

        for post in data.get('items', []):
            published = (post.get('published') or '')[:10]
            content = post.get('content') or ''
            source_url = post.get('url') or start_url
            for link in parse_traxdb_links_from_html(content, source_url, published or None):
                if link.list_id not in found:
                    found[link.list_id] = link

            if stop_at_or_before_date and published and published <= stop_at_or_before_date:
                stop = True
                break

        if stop:
            break

        page_token = data.get('nextPageToken')
        if not page_token:
            break

    class _LinksList(list):
        pass

    result = _LinksList(found.values())
    result._pages_scraped = pages_done
    return result
