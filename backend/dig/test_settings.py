"""Isolated Dig proof: no .env, production settings, DB service or workers.

python backend/manage.py test dig.test_beatport --settings=dig.test_settings
"""

SECRET_KEY = 'dig-tests-only'
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'core', 'wanted', 'dig']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
ROOT_URLCONF = 'dig.urls'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': []}
