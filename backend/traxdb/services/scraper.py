"""
Native blog scraper for TraxDB.

Replicated from tools/traxdb_sync/traxdb_scrape.py and sync.py.
Scrapes the blog for Pixeldrain list links, stores results in ScrapedFolder/ScrapedTrack models.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from typing import Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup
from django import db

from .pixeldrain import PixeldrainClient, PixeldrainFile, is_pixeldrain_not_found

logger = logging.getLogger(__name__)

PIXELDRAIN_LIST_RE = re.compile(r"https?://pixeldrain\.com/l/[A-Za-z0-9]+", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
MIRROR1_RE = re.compile(r"mirror\s*1\s*:\s*(https?://pixeldrain\.com/l/[A-Za-z0-9]+)", re.IGNORECASE)


@dataclass(frozen=True)
class TraxDBLink:
    pixeldrain_url: str
    list_id: str
    source_url: str
    inferred_date: Optional[str] = None


# ── HTML parsing helpers ──────────────────────────────────────


def _extract_iso_date(s: str) -> Optional[str]:
    m = ISO_DATE_RE.search(s)
    return m.group(1) if m else None


def _infer_date_from_text(text: str) -> Optional[str]:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def _infer_post_date(post_soup: BeautifulSoup) -> Optional[str]:
    t = post_soup.find("time")
    if t and t.get("datetime"):
        d = _extract_iso_date(t["datetime"])
        if d:
            return d

    ab = post_soup.find("abbr", class_=re.compile(r"published", re.I))
    if ab and ab.get("title"):
        d = _extract_iso_date(ab["title"])
        if d:
            return d

    prev_date = post_soup.find_previous(class_=re.compile(r"date-header", re.I))
    if prev_date:
        d = _extract_iso_date(prev_date.get_text(" ", strip=True))
        if d:
            return d

    for tag in post_soup.find_all(["time", "abbr", "span", "div"], limit=50):
        for attr in ("title", "datetime", "data-datetime"):
            v = tag.get(attr)
            if isinstance(v, str):
                d = _extract_iso_date(v)
                if d:
                    return d

    return _infer_date_from_text(post_soup.get_text(" ", strip=True))


def _pick_one_pixeldrain_url(post: BeautifulSoup) -> Optional[str]:
    text = post.get_text("\n", strip=True)
    m1 = MIRROR1_RE.search(text)
    if m1:
        return m1.group(1)

    m = PIXELDRAIN_LIST_RE.search(text)
    if m:
        return m.group(0)

    for a in post.find_all("a", href=True):
        href = a.get("href")
        if isinstance(href, str):
            m = PIXELDRAIN_LIST_RE.search(href)
            if m:
                return m.group(0)
    return None


# ── Cookie loading ────────────────────────────────────────────


def _load_cookies(cookie_path: str) -> requests.cookies.RequestsCookieJar:
    if not os.path.exists(cookie_path):
        raise FileNotFoundError(f"Cookies file not found: {cookie_path}")

    if cookie_path.lower().endswith(".json"):
        with open(cookie_path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        jar = requests.cookies.RequestsCookieJar()
        if not isinstance(data, list):
            raise ValueError("Cookie JSON must be a list of cookies")
        for c in data:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            value = c.get("value")
            domain = c.get("domain")
            path = c.get("path", "/")
            if name and value and domain:
                jar.set(name, value, domain=domain, path=path)
        return jar

    cj = MozillaCookieJar(cookie_path)
    cj.load(ignore_discard=True, ignore_expires=True)
    jar = requests.cookies.RequestsCookieJar()
    for c in cj:
        jar.set(c.name, c.value, domain=c.domain, path=c.path)
    return jar


def _make_session(*, cookies_path: Optional[str], user_agent: str = "traxdb_sync/1.0") -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent})
    if cookies_path:
        s.cookies.update(_load_cookies(cookies_path))
    return s


# ── Blog scraping ─────────────────────────────────────────────


def scrape_blog_links(
    session: requests.Session,
    *,
    start_url: str,
    max_pages: int = 50,
    stop_at_or_before_date: Optional[str] = None,
) -> List[TraxDBLink]:
    """Scrape a Blogspot page for Pixeldrain list links."""
    found: Dict[str, TraxDBLink] = {}
    next_url = start_url

    pages_done = 0
    for _ in range(max_pages):
        r = session.get(next_url, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Failed to fetch {next_url} ({r.status_code})")

        # Blogspot redirects private blogs to a Google sign-in page when our
        # cookies are stale. Status is still 200 but content is the login form
        # — if we don't catch this, the sync silently reports 0 new links.
        final_host = r.url.split('/', 3)[2] if '://' in r.url else ''
        if 'accounts.google.com' in final_host or 'blogger.com/blogin' in r.url:
            raise RuntimeError(
                f"Blog requires login — cookies stale or missing. "
                f"Refresh {os.environ.get('TRAXDB_COOKIES', 'TRAXDB_COOKIES')} "
                f"by re-exporting from a logged-in browser. "
                f"Final URL: {r.url[:200]}"
            )

        soup = BeautifulSoup(r.text, "html.parser")

        # The blog moved away from `div.post.hentry` containers to a flat
        # text format: each "post" is a date heading followed by a track list,
        # with `MIRROR1: https://pixeldrain.com/l/...` lines at the bottom.
        # We honour the old structure first (in case it ever returns) but fall
        # back to a regex scan over the full text so we keep working when the
        # template changes again.
        posts = soup.select("div.post.hentry") or soup.find_all("article", class_=re.compile(r"post", re.I))

        if posts:
            pages_done += 1
        else:
            # Flat-text mode — scan body for pixeldrain links and pair each one
            # with the nearest preceding ISO date header in the document text.
            body_text = soup.get_text("\n", strip=False)
            # Index every (position, date) pair so we can look up the nearest
            # heading before each link match.
            date_positions = [
                (m.start(), m.group(1))
                for m in re.finditer(r'(\b\d{4}-\d{2}-\d{2}\b)', body_text)
            ]

            link_iter = re.finditer(
                r'https?://pixeldrain\.com/l/([A-Za-z0-9]+)', body_text
            )
            for m in link_iter:
                list_id = m.group(1)
                if list_id in found:
                    continue
                # nearest preceding date
                inferred = None
                pos = m.start()
                for dpos, ddate in reversed(date_positions):
                    if dpos < pos:
                        inferred = ddate
                        break
                found[list_id] = TraxDBLink(
                    pixeldrain_url=m.group(0),
                    list_id=list_id,
                    source_url=next_url,
                    inferred_date=inferred,
                )
            pages_done += 1
            posts = []  # skip the structured-loop below

        oldest_post_date: Optional[str] = None
        for post in posts:
            post_date = _infer_post_date(post) if post is not soup else _infer_date_from_text(soup.get_text(" ", strip=True))
            if post_date and (oldest_post_date is None or post_date < oldest_post_date):
                oldest_post_date = post_date

            u = _pick_one_pixeldrain_url(post)
            if not u:
                continue
            list_id = u.split("/l/", 1)[1].split("?", 1)[0].split("#", 1)[0].strip("/")
            if list_id in found:
                continue
            found[list_id] = TraxDBLink(
                pixeldrain_url=u,
                list_id=list_id,
                source_url=next_url,
                inferred_date=post_date,
            )

        # Find next page link
        nxt = soup.find("a", attrs={"rel": "next"})
        if nxt and nxt.get("href"):
            next_url = nxt["href"]
        else:
            older = soup.find("a", id=re.compile(r"blog-pager-older-link", re.I)) or soup.find(
                "a", class_=re.compile(r"blog-pager-older-link", re.I)
            )
            if older and older.get("href"):
                next_url = older["href"]
            else:
                older2 = soup.find("a", string=re.compile(r"Older Posts", re.I))
                if older2 and older2.get("href"):
                    next_url = older2["href"]
                else:
                    next_url = None

        if stop_at_or_before_date and oldest_post_date and oldest_post_date <= stop_at_or_before_date:
            break

        if next_url:
            continue
        break

    # Wrap in a small subclass so callers can read pages_done without changing
    # the call sites that iterate the result like a list.
    class _LinksList(list):
        pass

    result = _LinksList(found.values())
    result._pages_scraped = pages_done
    return result


# ── Local inventory helpers ───────────────────────────────────

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _scan_local_inventory(traxdb_root: str):
    """Return (date_dirs, max_date, seen_list_ids, flac_basenames)."""
    date_dirs = []
    flac_basenames: Set[str] = set()

    if os.path.isdir(traxdb_root):
        for entry in os.scandir(traxdb_root):
            if entry.is_dir() and DATE_DIR_RE.match(entry.name):
                date_dirs.append(entry.name)
                for ext in ("*.flac", "*.wav", "*.aiff", "*.aif", "*.mp3"):
                    import glob
                    for f in glob.glob(os.path.join(entry.path, ext)):
                        flac_basenames.add(os.path.basename(f))

    date_dirs.sort()
    max_date = date_dirs[-1] if date_dirs else None

    seen_ids: Set[str] = set()
    seen_path = os.path.join(traxdb_root, ".pixeldrain_lists_seen.json")
    try:
        with open(seen_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                seen_ids = {str(x) for x in data}
    except Exception:
        pass

    return date_dirs, max_date, seen_ids, flac_basenames


def _mark_list_ids_seen(traxdb_root: str, list_ids: List[str]) -> None:
    seen_path = os.path.join(traxdb_root, ".pixeldrain_lists_seen.json")
    existing: Set[str] = set()
    try:
        with open(seen_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                existing = {str(x) for x in data}
    except Exception:
        existing = set()

    merged = sorted(existing | {str(x) for x in list_ids})
    tmp_path = seen_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(merged, indent=2, ensure_ascii=False, fp=f)
    os.replace(tmp_path, seen_path)


# ── Main sync entry point ────────────────────────────────────


def run_sync(operation_id: int, max_pages: int = 50):
    """Run blog scrape in a background thread. Stores results in DB models."""
    from ..models import TraxDBOperation, ScrapedFolder, ScrapedTrack

    try:
        op = TraxDBOperation.objects.get(id=operation_id)
        op.status = 'running'
        op.save()

        traxdb_root = os.environ.get('TRAXDB_ROOT', '/music/Electronic/ID3/traxdb')
        start_url = os.environ.get('TRAXDB_START_URL', '')
        pixeldrain_key = os.environ.get('PIXELDRAIN_API_KEY', '')
        cookies_path = os.environ.get('TRAXDB_COOKIES', '')

        if not start_url:
            op.status = 'failed'
            op.error_message = 'TRAXDB_START_URL not configured'
            op.save()
            return

        # Scan local inventory for cutoff date and seen list IDs
        date_dirs, max_date, seen_ids, flac_basenames = _scan_local_inventory(traxdb_root)

        # Also consider folders already in our DB as "seen"
        db_folder_ids = set(ScrapedFolder.objects.values_list('folder_id', flat=True))
        seen_ids = seen_ids | db_folder_ids

        session = _make_session(cookies_path=cookies_path or None)
        links = scrape_blog_links(
            session,
            start_url=start_url,
            max_pages=max_pages,
            stop_at_or_before_date=max_date,
        )

        # Filter: only new links not already seen
        new_links = [l for l in links if l.list_id not in seen_ids]

        # Cutoff filter
        cutoff_date = max_date
        skipped_by_cutoff = []
        if cutoff_date:
            kept = []
            for l in new_links:
                if l.inferred_date and l.inferred_date <= cutoff_date:
                    skipped_by_cutoff.append(l)
                else:
                    kept.append(l)
            new_links = kept

        # Query Pixeldrain for file plans if we have an API key
        client = PixeldrainClient(api_key=pixeldrain_key) if pixeldrain_key else None

        errors = []

        # Store new links as ScrapedFolder + ScrapedTrack records
        for link in new_links:
            try:
                folder, created = ScrapedFolder.objects.get_or_create(
                    folder_id=link.list_id,
                    defaults={
                        'pixeldrain_url': link.pixeldrain_url,
                        'url': link.source_url,
                        'inferred_date': link.inferred_date or '',
                        'pixeldrain_links': [link.pixeldrain_url],
                        'sync_operation': op,
                    }
                )

                # If we have a client, fetch file details
                if client and created:
                    try:
                        files = list(client.iter_list_files(link.list_id))
                        for pf in files:
                            ScrapedTrack.objects.create(
                                folder=folder,
                                filename=pf.name,
                                pixeldrain_file_id=pf.id,
                                pixeldrain_url=f"https://pixeldrain.com/api/file/{pf.id}",
                                file_size_bytes=pf.size,
                            )
                    except Exception as e:
                        if is_pixeldrain_not_found(e):
                            errors.append({
                                'list_id': link.list_id,
                                'error': f'Dead link (404): {repr(e)}',
                            })
                        else:
                            errors.append({
                                'list_id': link.list_id,
                                'error': repr(e),
                            })
            except Exception as e:
                errors.append({
                    'list_id': link.list_id,
                    'error': repr(e),
                })

        # Build summary compatible with existing frontend
        links_found_data = [
            {
                'list_id': l.list_id,
                'pixeldrain_url': l.pixeldrain_url,
                'source_url': l.source_url,
                'inferred_date': l.inferred_date,
            }
            for l in links
        ]
        links_new_data = [
            {
                'list_id': l.list_id,
                'pixeldrain_url': l.pixeldrain_url,
                'source_url': l.source_url,
                'inferred_date': l.inferred_date,
                'file_count': ScrapedFolder.objects.filter(folder_id=l.list_id).first().tracks.count()
                if ScrapedFolder.objects.filter(folder_id=l.list_id).exists() else 0,
            }
            for l in new_links
        ]

        op.summary = {
            'links_found_count': len(links),
            'links_new_count': len(new_links),
            'links_skipped_by_cutoff_date': len(skipped_by_cutoff),
            'pages_scraped': getattr(links, '_pages_scraped', max_pages),
            'errors_count': len(errors),
            'links_found': links_found_data,
            'links_new': links_new_data,
            'errors': errors,
        }

        if len(links) > 0 or len(errors) == 0:
            op.status = 'completed'
            if errors:
                op.error_message = f'{len(errors)} errors (dead links during plan-files)'
        else:
            op.status = 'failed'
            op.error_message = f'{len(errors)} errors during sync'

        op.save()
        logger.info(f'Sync #{operation_id} finished: {op.status}')

    except Exception as e:
        logger.exception(f'Sync #{operation_id} crashed')
        try:
            op = TraxDBOperation.objects.get(id=operation_id)
            op.status = 'failed'
            op.error_message = str(e)
            op.save()
        except Exception:
            pass
    finally:
        db.connections.close_all()
