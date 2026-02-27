from __future__ import annotations

import json
import re
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from typing import Dict, Iterable, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup


PIXELDRAIN_LIST_RE = re.compile(r"https?://pixeldrain\.com/l/[A-Za-z0-9]+", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
MIRROR1_RE = re.compile(r"mirror\s*1\s*:\s*(https?://pixeldrain\.com/l/[A-Za-z0-9]+)", re.IGNORECASE)


class TraxDBError(RuntimeError):
    pass


@dataclass(frozen=True)
class TraxDBLink:
    pixeldrain_url: str
    list_id: str
    source_url: str
    inferred_date: Optional[str] = None  # YYYY-MM-DD if we can infer it


def _infer_date_from_text(text: str) -> Optional[str]:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def _extract_iso_date(s: str) -> Optional[str]:
    m = ISO_DATE_RE.search(s)
    return m.group(1) if m else None


def _infer_post_date(post_soup: BeautifulSoup) -> Optional[str]:
    """
    Try common Blogger patterns:
    - <time datetime="2025-10-24T...">
    - <abbr class="published" title="2025-10-24T...">
    - any attribute containing an ISO date
    """
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

    # Blogger templates often put an ISO date in a date header outside the post body:
    # <h2 class='date-header'><span>2025-12-01</span></h2>
    prev_date = post_soup.find_previous(class_=re.compile(r"date-header", re.I))
    if prev_date:
        d = _extract_iso_date(prev_date.get_text(" ", strip=True))
        if d:
            return d

    # Fallback: scan a few likely attrs
    for tag in post_soup.find_all(["time", "abbr", "span", "div"], limit=50):
        for attr in ("title", "datetime", "data-datetime"):
            v = tag.get(attr)
            if isinstance(v, str):
                d = _extract_iso_date(v)
                if d:
                    return d

    # Last resort: text scrape
    return _infer_date_from_text(post_soup.get_text(" ", strip=True))


def _pick_one_pixeldrain_url(post: BeautifulSoup) -> Optional[str]:
    """
    TraxDB posts sometimes include multiple Pixeldrain links (e.g. MIRROR1/MIRROR2).
    User preference: pick a single URL per post to avoid mirror churn.

    Priority:
    1) A link explicitly labeled MIRROR1 in the post text
    2) First Pixeldrain list URL found in the post text
    3) First Pixeldrain list URL found in <a href="...">
    """
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


def load_cookies(cookie_path: str) -> requests.cookies.RequestsCookieJar:
    """
    Supports:
    - Netscape cookies.txt (MozillaCookieJar)
    - A simple JSON list of cookie dicts: [{name,value,domain,path,...}, ...]
    """
    try:
        import os

        if not os.path.exists(cookie_path):
            raise TraxDBError(f"Cookies file not found: {cookie_path}")
    except TraxDBError:
        raise
    except Exception:
        # If os/path checks fail for some reason, fall through and let parsing raise.
        pass

    if cookie_path.lower().endswith(".json"):
        with open(cookie_path, "r", encoding="utf-8") as _f:
            data = json.loads(_f.read())
        jar = requests.cookies.RequestsCookieJar()
        if not isinstance(data, list):
            raise TraxDBError("Cookie JSON must be a list of cookies")
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
    try:
        cj.load(ignore_discard=True, ignore_expires=True)
    except FileNotFoundError:
        raise TraxDBError(f"Cookies file not found: {cookie_path}")
    except Exception as e:
        raise TraxDBError(f"Failed to load cookies from {cookie_path}: {e}")
    jar = requests.cookies.RequestsCookieJar()
    for c in cj:
        jar.set(c.name, c.value, domain=c.domain, path=c.path)
    return jar


def make_session(*, cookies_path: Optional[str], user_agent: str = "traxdb_sync/1.0") -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent})
    if cookies_path:
        s.cookies.update(load_cookies(cookies_path))
    return s


def scrape_pixeldrain_list_links(
    session: requests.Session,
    *,
    start_url: str,
    max_pages: int = 10,
    stop_at_or_before_date: Optional[str] = None,
) -> List[TraxDBLink]:
    """
    Scrape a Blogspot search page (or any TraxDB page) for Pixeldrain list links.

    Works best when:
    - start_url is a TraxDB search URL, e.g. https://traxdb2.blogspot.com/search?updated-max=...
    - and you provide cookies if the blog is private.
    """
    found: Dict[str, TraxDBLink] = {}
    next_url = start_url

    for _ in range(max_pages):
        r = session.get(next_url, timeout=60)
        if r.status_code != 200:
            raise TraxDBError(f"Failed to fetch {next_url} ({r.status_code})")

        soup = BeautifulSoup(r.text, "html.parser")

        # Extract top-level post containers only (avoid wrappers like blog-posts/date-posts/post-outer/post-body).
        posts = soup.select("div.post.hentry") or soup.find_all("article", class_=re.compile(r"post", re.I))
        if not posts:
            posts = [soup]

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

        # Blogger "next page" links often include rel="next"
        nxt = soup.find("a", attrs={"rel": "next"})
        if nxt and nxt.get("href"):
            next_url = nxt["href"]
        else:
            # Blogger classic pager ids/classes
            older = soup.find("a", id=re.compile(r"blog-pager-older-link", re.I)) or soup.find(
                "a", class_=re.compile(r"blog-pager-older-link", re.I)
            )
            if older and older.get("href"):
                next_url = older["href"]
            else:
                # fallback: look for older posts link text
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

    return list(found.values())


def filter_new_links(
    links: Iterable[TraxDBLink],
    *,
    already_seen_list_ids: Set[str],
) -> List[TraxDBLink]:
    out: List[TraxDBLink] = []
    for l in links:
        if l.list_id not in already_seen_list_ids:
            out.append(l)
    return out


