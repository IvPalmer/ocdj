"""Authentication for the local YouTube worker bridge."""
import functools
import hmac
import os

from rest_framework.response import Response
from rest_framework import status


def require_youtube_worker_token(view):
    """Allow only the operator's local downloader to claim/upload jobs."""
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        expected = (os.environ.get('YOUTUBE_WORKER_TOKEN') or '').strip()
        if not expected:
            return Response(
                {'error': 'local YouTube worker is not configured'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        presented = request.headers.get('Authorization', '')
        if not presented.startswith('Bearer '):
            return Response(
                {'error': 'missing bearer token'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        token = presented[7:].strip()
        if not hmac.compare_digest(token, expected):
            return Response(
                {'error': 'invalid worker token'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return view(request, *args, **kwargs)
    return wrapped
