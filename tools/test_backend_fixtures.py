"""Full PostgreSQL backend discovery for Actions; do not run on an operator Mac.

The existing Dig fixture documents a standalone URL root (dig.test_settings).
Only that class gets its intended root; all other tests keep project settings.
No Dig runtime or fixture is changed, excluded, skipped or copied.
"""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
backend = root / 'backend'
os.chdir(backend)
sys.path[:0] = [str(backend), str(root)]
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djtools_project.settings')

import django
from django.core.management import call_command
from django.test import override_settings

django.setup()
from dig.test_beatport import BeatportCaptureTests

override_settings(ROOT_URLCONF='dig.urls')(BeatportCaptureTests)
call_command('test', interactive=False, verbosity=2)
