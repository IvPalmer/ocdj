#!/usr/bin/env python3
"""Refresh traxdb_cookies.txt from your local Chrome session.

Reads Chrome's cookie store (decrypted via macOS keychain), filters to the
domains needed for blogspot.com / google.com auth, and writes a Netscape-format
cookie file the scraper can consume.

Run from the repo root:
    python3 tools/traxdb_sync/refresh_cookies.py
"""
import os
import sys
import time
from http.cookiejar import MozillaCookieJar, Cookie

try:
    import browser_cookie3
except ImportError:
    sys.exit("browser-cookie3 not installed. Run: pip3 install --user browser-cookie3")

OUT = os.path.join(os.path.dirname(__file__), 'traxdb_cookies.txt')

# Domains we need cookies for: the blog itself + Google auth machinery.
DOMAIN_KEEP = ('blogspot.com', '.google.com', 'google.com', 'blogger.com', '.blogger.com')


def _normalize_expires(value):
    """browser_cookie3 sometimes leaves expires as None (session cookie). The
    Netscape format needs an int — use 0 to mark session cookies (most parsers
    skip those, but our session uses MozillaCookieJar with ignore_expires)."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def main():
    # Pull from Chrome by default; fall back to merging multiple browsers if Chrome empty.
    print('Reading cookies from Chrome…')
    cj_chrome = browser_cookie3.chrome(domain_name='blogspot.com')

    jar = MozillaCookieJar(OUT)
    kept = 0
    for c in cj_chrome:
        if not any(c.domain.endswith(d) or c.domain == d for d in DOMAIN_KEEP):
            continue
        jar.set_cookie(Cookie(
            version=c.version or 0,
            name=c.name,
            value=c.value or '',
            port=None,
            port_specified=False,
            domain=c.domain,
            domain_specified=bool(c.domain),
            domain_initial_dot=c.domain.startswith('.'),
            path=c.path or '/',
            path_specified=bool(c.path),
            secure=bool(c.secure),
            expires=_normalize_expires(c.expires),
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        ))
        kept += 1

    # Also pull google.com cookies (auth lives there)
    print('Reading google.com cookies from Chrome…')
    for c in browser_cookie3.chrome(domain_name='google.com'):
        if not any(c.domain.endswith(d) or c.domain == d for d in DOMAIN_KEEP):
            continue
        jar.set_cookie(Cookie(
            version=c.version or 0,
            name=c.name,
            value=c.value or '',
            port=None,
            port_specified=False,
            domain=c.domain,
            domain_specified=bool(c.domain),
            domain_initial_dot=c.domain.startswith('.'),
            path=c.path or '/',
            path_specified=bool(c.path),
            secure=bool(c.secure),
            expires=_normalize_expires(c.expires),
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        ))
        kept += 1

    jar.save(ignore_discard=True, ignore_expires=True)
    print(f'Wrote {kept} cookies to {OUT}')
    print(f'File size: {os.path.getsize(OUT)} bytes, mtime: {time.ctime(os.path.getmtime(OUT))}')


if __name__ == '__main__':
    main()
