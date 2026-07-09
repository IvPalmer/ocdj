#!/usr/bin/env python3
"""Bootstrap (or re-bootstrap) the TraxDB Blogger API OAuth refresh token.

The TraxDB sync's default fetch path (`TRAXDB_FETCH_MODE=api`) authenticates to
the private Blogspot blog with the official Blogger API v3 and a durable OAuth
refresh token — no browser cookies. This script mints that refresh token via the
one-time loopback consent flow. Run it on the Mac (needs a browser + the
operator logged into the reader Google account), then load the three values into
the OCDJ config store.

You need the OAuth **Desktop app** client from GCP project ``ocdj-traxdb``
(client id ``70890669268-ies9haklu1i1s88m5pfmh0jlo47qriut.apps.googleusercontent.com``).
Download its client JSON from the GCP console (APIs & Services → Credentials →
the desktop client → "Download JSON") and pass the path with ``--client-json``.

The consent screen for this project is published to **Production** (NOT left in
"Testing"), which is what makes the refresh token durable — Testing-mode tokens
silently expire after 7 days. ``blogger.readonly`` is a sensitive scope, so
personal use goes through the "unverified app" warning screen; that is expected
and fine (click through "Advanced → Go to … (unsafe)").

Usage:
    python3 tools/traxdb_sync/blogger_oauth_bootstrap.py \
        --client-json ~/Downloads/client_secret_....json \
        --out ~/traxdb_refresh_token.txt

The refresh token is written to ``--out`` (required; pick a path OUTSIDE the
repo) and NEVER printed to stdout. After it succeeds, load the three values
into config (DB config store beats env / settings):

    BLOGGER_CLIENT_ID      = <client_id     from the client JSON>
    BLOGGER_CLIENT_SECRET  = <client_secret from the client JSON>
    BLOGGER_REFRESH_TOKEN  = <contents of the --out file>

Then set ``TRAXDB_FETCH_MODE=api`` (already the default) and run a sync.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/blogger.readonly"


def _load_client(client_json_path: str) -> tuple[str, str]:
    """Extract (client_id, client_secret) from a downloaded Google client JSON."""
    with open(client_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Desktop / installed-app clients nest under "installed"; some exports use
    # "web". Accept either.
    block = data.get("installed") or data.get("web") or data
    client_id = block.get("client_id")
    client_secret = block.get("client_secret")
    if not client_id or not client_secret:
        sys.exit(f"client_id/client_secret not found in {client_json_path}")
    return client_id, client_secret


class _CodeCatcher(BaseHTTPRequestHandler):
    """Serve exactly one request to capture the ?code= from the redirect."""

    code: str | None = None
    error: str | None = None

    def do_GET(self):  # noqa: N802 (stdlib naming)
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        if "code" in params:
            _CodeCatcher.code = params["code"][0]
            body = b"TraxDB Blogger OAuth: code captured. You can close this tab."
        else:
            _CodeCatcher.error = params.get("error", ["unknown"])[0]
            body = b"TraxDB Blogger OAuth: authorization failed. Check the terminal."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence the default request logging
        pass


def _exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode()
    req = urllib.request.Request(TOKEN_ENDPOINT, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    ap = argparse.ArgumentParser(description="Mint a Blogger API refresh token via loopback OAuth.")
    ap.add_argument("--client-json", required=True,
                    help="Path to the downloaded Google OAuth desktop client JSON.")
    ap.add_argument("--out", required=True,
                    help="Where to write the refresh token (pick a path outside the repo).")
    args = ap.parse_args()

    client_id, client_secret = _load_client(args.client_json)

    # Bind a loopback socket on an ephemeral port for the redirect URI.
    server = HTTPServer(("127.0.0.1", 0), _CodeCatcher)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}"

    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # force a refresh_token even on re-consent
    })

    print("Opening the consent screen in your browser…")
    print("If it doesn't open, paste this URL manually:\n")
    print("  " + auth_url + "\n")
    print("(This is a sensitive scope: click Advanced → Go to … (unsafe) to proceed.)")
    webbrowser.open(auth_url)

    # Serve exactly one request (the redirect back with ?code=).
    server.handle_request()
    server.server_close()

    if _CodeCatcher.error or not _CodeCatcher.code:
        sys.exit(f"Authorization failed: {_CodeCatcher.error or 'no code returned'}")

    tokens = _exchange_code(client_id, client_secret, _CodeCatcher.code, redirect_uri)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        sys.exit(
            "No refresh_token in the token response. Google only returns one on "
            "first consent — revoke the app's access at "
            "https://myaccount.google.com/permissions and re-run."
        )

    # Write to file with tight permissions; never echo the secret to stdout.
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(refresh_token + "\n")

    print("\nRefresh token written to:", args.out)
    print("Load these into the OCDJ config store (do NOT commit them):")
    print("  BLOGGER_CLIENT_ID     =", client_id)
    print("  BLOGGER_CLIENT_SECRET = <from the client JSON>")
    print("  BLOGGER_REFRESH_TOKEN = <contents of", os.path.basename(args.out) + ">")


if __name__ == "__main__":
    main()
