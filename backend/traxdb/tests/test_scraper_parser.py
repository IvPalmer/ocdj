"""Regression tests for the shared HTML → links parser."""
from django.test import SimpleTestCase

from traxdb.services.scraper import parse_traxdb_links_from_html

SOURCE = "https://traxdb2.blogspot.com"


class ParseTraxDBLinksTestCase(SimpleTestCase):
    def test_mirror1_link_uses_fallback_date(self):
        # A single post body (Blogger API `content` shape): MIRROR1 line, no
        # ISO date in the body text, so the post's published date is the
        # fallback.
        body = (
            "<div>Various Artists - Some Compilation<br>"
            "01. Track One<br>02. Track Two<br>"
            "MIRROR1: https://pixeldrain.com/l/abc123XY<br>"
            "MIRROR2: https://example.com/other</div>"
        )
        links = parse_traxdb_links_from_html(body, SOURCE, "2026-07-01")

        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual(link.list_id, "abc123XY")
        self.assertEqual(link.pixeldrain_url, "https://pixeldrain.com/l/abc123XY")
        self.assertEqual(link.source_url, SOURCE)
        self.assertEqual(link.inferred_date, "2026-07-01")

    def test_inline_date_beats_fallback(self):
        body = (
            "<div>2026-06-15 Release Day<br>"
            "https://pixeldrain.com/l/dateWins9</div>"
        )
        links = parse_traxdb_links_from_html(body, SOURCE, "2026-07-01")

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].inferred_date, "2026-06-15")

    def test_href_only_link_fallback(self):
        # URL present only as an attribute, not visible text.
        body = '<div><a href="https://pixeldrain.com/l/hrefOnly1">download</a></div>'
        links = parse_traxdb_links_from_html(body, SOURCE, "2026-05-05")

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].list_id, "hrefOnly1")
        self.assertEqual(links[0].inferred_date, "2026-05-05")

    def test_dedupe_by_list_id(self):
        body = (
            "<div>MIRROR1: https://pixeldrain.com/l/dupe0001<br>"
            "again https://pixeldrain.com/l/dupe0001</div>"
        )
        links = parse_traxdb_links_from_html(body, SOURCE, None)
        self.assertEqual(len(links), 1)

    def test_empty_body_returns_empty(self):
        self.assertEqual(parse_traxdb_links_from_html("", SOURCE, "2026-07-01"), [])

    def test_no_link_body_returns_empty(self):
        body = "<div>Just some text, no links here at all.</div>"
        self.assertEqual(parse_traxdb_links_from_html(body, SOURCE, "2026-07-01"), [])
