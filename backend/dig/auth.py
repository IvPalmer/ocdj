"""Bearer token auth for the browser extension's endpoints.

A separate token from DRAIN_TOKEN on purpose. This one is pasted into a
browser extension's settings, which is a far softer place to keep a secret than
a LaunchAgent's config file — a compromised browser should not also hand over
the drain endpoints that move audio between machines.

Needed because Safari will not send the oauth2-proxy session cookie on a fetch
from a web extension's own origin: the request reaches the server and comes
back 401, so the extension silently fell back to browser-local storage while
appearing to save. Cookies cannot be made to work here; a token can.
"""
import hmac
import os

from rest_framework import status as http_status
from rest_framework.response import Response


def _server_token():
    return (os.environ.get('DIG_TOKEN') or '').strip()


def require_dig_token(view):
    """Reject unless the caller presents the DIG_TOKEN as a bearer token.

    Fails closed: with no token configured the endpoints are disabled rather
    than open, so a misconfigured deploy cannot expose them. They sit on a
    Traefik path that skips oauth2-proxy, and this is the only thing guarding
    them.
    """
    def wrapper(request, *args, **kwargs):
        server_token = _server_token()
        if not server_token:
            return Response(
                {'error': 'dig API disabled: DIG_TOKEN not configured'},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if not header.startswith('Bearer '):
            return Response({'error': 'missing bearer token'},
                            status=http_status.HTTP_401_UNAUTHORIZED)
        if not hmac.compare_digest(header[len('Bearer '):].strip(), server_token):
            return Response({'error': 'invalid token'},
                            status=http_status.HTTP_401_UNAUTHORIZED)
        return view(request, *args, **kwargs)

    wrapper.__name__ = getattr(view, '__name__', 'wrapped')
    wrapper.__doc__ = view.__doc__
    return wrapper
