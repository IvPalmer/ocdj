"""
slskd API client and matching engine.

Talks to slskd via REST API (runs as sibling Docker container).
"""
import re
import time
import logging
import unicodedata
import requests
from django.conf import settings
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# Default timeout for all slskd HTTP calls. Searches can be slow, so give polling
# calls more headroom; other calls fail fast.
DEFAULT_TIMEOUT = 30


# ── slskd API Client ─────────────────────────────────────────

class SlskdClient:
    """Thin wrapper around slskd REST API."""

    def __init__(self):
        self.base_url = settings.SLSKD_BASE_URL.rstrip('/')
        self.api_key = settings.SLSKD_API_KEY
        self.session = requests.Session()
        self.session.headers.update({
            'X-API-Key': self.api_key,
            'Content-Type': 'application/json',
        })

    def _url(self, path):
        return f"{self.base_url}/api/v0{path}"

    def health(self):
        """Check if slskd is reachable."""
        try:
            r = self.session.get(self._url('/application'), timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"slskd health check failed: {e}")
            return None

    def search(self, query, timeout=15000, retries=3):
        """Start a search and return the search ID. Retries on 429."""
        for attempt in range(retries):
            r = self.session.post(self._url('/searches'), json={
                'searchText': query,
                'searchTimeout': timeout,
            }, timeout=DEFAULT_TIMEOUT)
            if r.status_code == 429:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(f"slskd 429 rate limit, retrying in {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        # Final attempt — let it raise
        r.raise_for_status()
        return r.json()

    def get_search(self, search_id, include_responses=True):
        """Get search results by ID. Must pass includeResponses=true to get file data."""
        params = {}
        if include_responses:
            params['includeResponses'] = 'true'
        r = self.session.get(self._url(f'/searches/{search_id}'), params=params, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def delete_search(self, search_id):
        """Delete a completed search."""
        r = self.session.delete(self._url(f'/searches/{search_id}'), timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()

    def download(self, username, filename, size=0):
        """Queue a file for download. slskd expects an array of file objects."""
        payload = [{'filename': filename}]
        if size:
            payload[0]['size'] = size
        r = self.session.post(self._url(f'/transfers/downloads/{username}'), json=payload, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def get_downloads(self):
        """Get all current downloads."""
        r = self.session.get(self._url('/transfers/downloads'), timeout=10)
        r.raise_for_status()
        return r.json()

    def get_user_downloads(self, username):
        """Get downloads from a specific user."""
        r = self.session.get(self._url(f'/transfers/downloads/{username}'), timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def browse_user(self, username):
        """Browse a Soulseek user's full share. Returns {directories: [...]}.

        Can be slow (peer must respond) and the payload may be tens of MB for
        users with huge shares — caller should consider scoping the result
        before returning it to the frontend.
        """
        r = self.session.get(self._url(f'/users/{username}/browse'), timeout=120)
        r.raise_for_status()
        return r.json()

    def cancel_download(self, username, transfer_id, remove=False):
        """Cancel a download transfer. If remove=True, also remove from slskd."""
        # Cancel the transfer
        r = self.session.put(
            self._url(f'/transfers/downloads/{username}/{transfer_id}'),
            json={'state': 'Completed, Cancelled'},
            timeout=DEFAULT_TIMEOUT,
        )
        r.raise_for_status()
        if remove:
            try:
                self.session.delete(
                    self._url(f'/transfers/downloads/{username}/{transfer_id}'),
                    timeout=DEFAULT_TIMEOUT,
                )
            except Exception:
                pass
        return True


# ── Matching Engine ───────────────────────────────────────────

# Common patterns to strip from queries
STRIP_PATTERNS = [
    r'\(feat\.?\s+[^)]+\)',    # (feat. Someone)
    r'\(ft\.?\s+[^)]+\)',      # (ft. Someone)
    r'\(remix\)',               # (remix)
    r'\(original mix\)',        # (original mix)
    r'\[.*?\]',                # [anything in brackets]
]

AUDIO_EXTENSIONS = {'mp3', 'flac', 'wav', 'aiff', 'aif', 'ogg', 'opus', 'wma', 'aac', 'm4a'}


def normalize_text(text):
    """Normalize text for matching: lowercase, strip special chars."""
    if not text:
        return ''
    text = text.lower().strip()
    for pattern in STRIP_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# Words that are noise in slsk queries (artist credits inflate the term count
# and shrink result sets — Soulseek prefers fewer, more generic terms).
_QUERY_NOISE_WORDS = {
    'feat', 'ft', 'featuring', 'with',
    'remix', 'mix', 'edit', 'version', 'extended', 'original',
    'vip', 'instrumental', 'radio', 'club', 'dub',
    'and',
    # Single-letter junk left by stripping apostrophes/punctuation
    'a', 'i', 'o', 's', 't', 'm', 'd', 'll', 're', 've',
}


def simplify_query(text, max_tokens=6):
    """Aggressive normalization for slsk search queries.

    Soulseek matches on substring tokens — punctuation, hyphens, accents and
    bracketed credits all hurt recall, and 6+ token queries return ~0 results
    because no single uploader's filename contains every token. This:
      - strips bracketed credits / mix annotations
      - ASCII-folds accents (Sébastien → Sebastien)
      - replaces all non-alphanumerics with spaces
      - drops noise words (feat, remix, mix, original, …)
      - keeps only the first `max_tokens` tokens to stay slsk-friendly
    """
    if not text:
        return ''

    # Strip bracketed credits / mix annotations BEFORE removing punctuation,
    # otherwise '(feat. X)' would survive as 'feat x'.
    cleaned = text
    for pattern in STRIP_PATTERNS:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)

    # ASCII-fold accents: "Sébastien" -> "Sebastien"
    cleaned = unicodedata.normalize('NFKD', cleaned)
    cleaned = ''.join(ch for ch in cleaned if not unicodedata.combining(ch))

    # Anything that isn't a letter or digit becomes a space.
    cleaned = re.sub(r'[^a-zA-Z0-9]+', ' ', cleaned).lower()

    # Drop noise words. Single-letter artist names (Mr. G, K-Hand) survive
    # because they're not in the noise list.
    tokens = [t for t in cleaned.split() if t and t not in _QUERY_NOISE_WORDS]

    if max_tokens and len(tokens) > max_tokens:
        tokens = tokens[:max_tokens]

    return ' '.join(tokens).strip()[:80]


def generate_queries(artist, title, release_name='', catalog_number='', label=''):
    """
    Generate search query variations for slskd.

    Soulseek is sensitive to over-specific queries — too many terms = no results.
    Strategy: try the most precise identifiers first, then broaden.
    Catalog numbers are gold (people name folders with them).

    All query parts run through simplify_query() which strips hyphens, accents,
    and punctuation — slsk indexes plain tokens, so 'Sébastien Léger - Pyt'
    matches more uploads as 'sebastien leger pyt'.
    """
    queries = []
    artist_clean = simplify_query(artist)
    title_clean = simplify_query(title)
    release_clean = simplify_query(release_name)
    catalog_clean = simplify_query(catalog_number)
    label_clean = simplify_query(label)

    # 1. Catalog number (if available) — very precise, great hit rate
    if catalog_clean:
        queries.append(catalog_clean)

    # 2. Artist + title (the classic combo)
    if artist_clean and title_clean:
        queries.append(f"{artist_clean} {title_clean}")

    # 3. Artist + release name (for finding full releases)
    if artist_clean and release_clean and release_clean != title_clean:
        queries.append(f"{artist_clean} {release_clean}")

    # 4. Just title (handles artist name variations)
    if title_clean:
        queries.append(title_clean)

    # 5. Just release name (if no title)
    if release_clean and not title_clean:
        queries.append(release_clean)

    # 6. Label + catalog (label folders are common)
    if label_clean and catalog_clean:
        queries.append(f"{label_clean} {catalog_clean}")

    # 7. Artist only (broadest, finds whole discographies)
    if artist_clean and len(artist_clean) > 3:
        queries.append(artist_clean)

    # Re-cap combined queries at the final stage — slsk's recall drops sharply
    # past ~6 tokens because no single uploader's filename hits every term.
    capped = []
    for q in queries:
        toks = q.split()
        if len(toks) > 6:
            toks = toks[:6]
        capped.append(' '.join(toks))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for q in capped:
        if q and q not in seen:
            seen.add(q)
            unique.append(q)

    return unique


def extract_file_info(filename):
    """Extract extension and basic info from a filename."""
    parts = filename.rsplit('.', 1)
    ext = parts[-1].lower() if len(parts) > 1 else ''
    return {
        'extension': ext,
        'is_audio': ext in AUDIO_EXTENSIONS,
    }


def score_result(artist, title, filename, release_name='', catalog_number=''):
    """
    Score a search result against wanted item metadata.
    Returns 0-100 score using weighted fuzzy matching.

    Checks filename and parent folder against artist, title,
    release name, and catalog number.
    """
    artist_norm = normalize_text(artist)
    title_norm = normalize_text(title)
    release_norm = normalize_text(release_name)
    catalog_norm = normalize_text(catalog_number)

    # Extract just the filename (no path)
    basename = filename.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]
    basename_clean = normalize_text(basename.rsplit('.', 1)[0])

    # Also check parent folder (often has artist name, label, catalog)
    parts = filename.replace('\\', '/').split('/')
    folder = normalize_text(parts[-2]) if len(parts) >= 2 else ''
    full_path_norm = normalize_text(filename)

    scores = []

    # Catalog number match — very high confidence if found in path
    if catalog_norm and len(catalog_norm) >= 3:
        if catalog_norm in full_path_norm:
            scores.append(90)  # Near-certain match

    if artist_norm and title_norm:
        # Full match against filename
        full_query = f"{artist_norm} {title_norm}"
        full_score = fuzz.token_set_ratio(full_query, basename_clean)
        scores.append(full_score)

        # Title in filename + artist in folder
        title_in_file = fuzz.token_set_ratio(title_norm, basename_clean)
        artist_in_folder = fuzz.token_set_ratio(artist_norm, folder) if folder else 0
        combined = (title_in_file * 0.6) + (artist_in_folder * 0.4)
        scores.append(combined)

    elif title_norm:
        title_score = fuzz.token_set_ratio(title_norm, basename_clean)
        scores.append(title_score)

    elif artist_norm:
        artist_score = fuzz.token_set_ratio(artist_norm, basename_clean)
        scores.append(artist_score * 0.7)  # Penalty for no title match

    # Release name matching (check folder — releases are usually in folders)
    if release_norm and folder:
        release_in_folder = fuzz.token_set_ratio(release_norm, folder)
        if artist_norm:
            artist_in_path = fuzz.token_set_ratio(artist_norm, full_path_norm)
            combined = (release_in_folder * 0.6) + (artist_in_path * 0.4)
            scores.append(combined)
        else:
            scores.append(release_in_folder * 0.8)

    return max(scores) if scores else 0


def filter_results(results, quality_preset=None):
    """Filter search results by quality preferences."""
    filtered = []

    for result in results:
        file_info = extract_file_info(result.get('filename', ''))

        # Must be audio
        if not file_info['is_audio']:
            continue

        size_mb = result.get('size', 0) / (1024 * 1024)

        if quality_preset:
            # Size filters
            if quality_preset.min_file_size_mb and size_mb < quality_preset.min_file_size_mb:
                continue
            if quality_preset.max_file_size_mb and size_mb > quality_preset.max_file_size_mb:
                continue

            # Bitrate filter for lossy
            bitrate = result.get('bitRate', 0)
            if file_info['extension'] == 'mp3' and bitrate and bitrate < quality_preset.min_bitrate:
                continue

        filtered.append(result)

    return filtered
