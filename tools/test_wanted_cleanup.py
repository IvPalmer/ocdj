"""Run synthetic cleanup research fixtures without project settings, DB or API.

Usage: python tools/test_wanted_cleanup.py
Install the repository's Django, requests, rapidfuzz and beautifulsoup4 requirements.
The latter two are needed by Wanted's existing service package imports.
"""
from pathlib import Path
import sys
import unittest

from django.conf import settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
settings.configure(
    SECRET_KEY='synthetic-fixture-only', INSTALLED_APPS=['wanted'], DATABASES={},
    DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
)
import django

django.setup()
suite = unittest.defaultTestLoader.loadTestsFromNames([
    'wanted.test_cleanup_research', 'wanted.test_cleanup_catalog',
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
