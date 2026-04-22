"""Bearer token auth for drain endpoints.

Token lives in the DRAIN_TOKEN env var on the VPS. Mac drain daemon sends:
  Authorization: Bearer <token>
Constant-time compare avoids timing leaks. Missing/empty token on server side
means the endpoints are disabled (return 503) — guards against a misconfigured
deploy accidentally exposing the API unauthenticated.
"""

import hmac
import os

from rest_framework.response import Response
from rest_framework import status as http_status


def _get_server_token():
    return (os.environ.get('DRAIN_TOKEN') or '').strip()


def require_drain_token(view):
    """Decorator: rejects if header token missing/invalid."""
    def wrapper(request, *args, **kwargs):
        server_token = _get_server_token()
        if not server_token:
            return Response(
                {'error': 'drain API disabled: DRAIN_TOKEN not configured'},
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
