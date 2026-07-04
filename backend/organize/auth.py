"""Bearer token auth for the pipeline-kick endpoint.

Token lives in the KICK_TOKEN env var on the VPS. Mac incoming daemon sends:
  Authorization: Bearer <token>
Constant-time compare avoids timing leaks. Missing/empty token on server side
means the endpoint is disabled (return 503) — guards against a misconfigured
deploy accidentally exposing the API unauthenticated.

Mirrors drain/auth.py's require_drain_token; pipeline/kick/ sits behind its
own Traefik priority-300 bypass router (ocdj-kick in
/etc/dokploy/traefik/dynamic/ocdj-auth.yml) so it needs the same in-app
belt-and-suspenders check that /api/drain/* already has.
"""

import hmac
import os

from rest_framework.response import Response
from rest_framework import status as http_status


def _get_server_token():
    return (os.environ.get('KICK_TOKEN') or '').strip()


def require_kick_token(view):
    """Decorator: rejects if header token missing/invalid."""
    def wrapper(request, *args, **kwargs):
        server_token = _get_server_token()
        if not server_token:
            return Response(
                {'error': 'kick API disabled: KICK_TOKEN not configured'},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if not header.startswith('Bearer '):
            return Response(
                {'error': 'missing bearer token'},
                status=http_status.HTTP_401_UNAUTHORIZED,
            )
        presented = header[len('Bearer '):].strip()
        if not hmac.compare_digest(presented, server_token):
            return Response(
                {'error': 'invalid token'},
                status=http_status.HTTP_401_UNAUTHORIZED,
            )
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    wrapper.__doc__ = view.__doc__
    return wrapper
