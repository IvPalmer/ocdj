import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


class BandcampCollector:
    """Lightweight Bandcamp search helper to find a release URL.

    Strategy:
    1) Use Bandcamp's public search page and parse the first album link
       matching the artist and album.
    2) Fall back to the first album link if fuzzy matching fails.
    """

    SEARCH_URL = "https://bandcamp.com/search?q={query}&item_type=a"

    def find_release_link(self, artist: str, album: str) -> Optional[str]:
        try:
            query = f"{artist} {album}".strip()
            url = self.SEARCH_URL.format(query=requests.utils.quote(query))
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            # Typical selector: <li class="searchresult"> ... <a href="https://label.bandcamp.com/album/slug">
            candidates = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "bandcamp.com/album/" in href:
                    text = (a.get_text(" ") or "").strip()
                    candidates.append((href, text))

            if not candidates:
                return None

            query_norm = (artist + " " + album).lower()
            # Prefer links whose text contains both artist and album words
            def score(item):
                href, text = item
                text_norm = f"{href} {text}".lower()
                score = 0
                for part in re.split(r"\s+", query_norm):
                    if part and part in text_norm:
                        score += 1
                return score

            candidates.sort(key=score, reverse=True)
            return candidates[0][0]
        except Exception as e:
            logger.debug(f"Bandcamp search failed: {e}")
            return None


