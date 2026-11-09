"""Pure synthetic catalog/policy proof: no Django setup, settings, DB or HTTP.

Bypass the existing wanted.services eager importer, which loads Django models.
Integration and real row-lock proofs run only in GitHub Actions/PostgreSQL.
"""
from pathlib import Path
import sys
import types
import unittest

backend = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(backend))
services = types.ModuleType('wanted.services')
services.__path__ = [str(backend / 'wanted' / 'services')]
sys.modules['wanted.services'] = services
suite = unittest.defaultTestLoader.loadTestsFromName('wanted.test_cleanup_evidence')
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
