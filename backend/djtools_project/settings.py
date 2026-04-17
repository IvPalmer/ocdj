"""
Django settings for djtools_project.
"""

from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from project root (one level up from backend/)
load_dotenv(dotenv_path=BASE_DIR.parent / '.env')

# SECRET_KEY: Generate a random key if not set via environment.
_default_secret = os.getenv('SECRET_KEY', '')
if not _default_secret:
    import secrets as _secrets
    _secret_file = BASE_DIR / '.secret_key'
    if _secret_file.exists():
        _default_secret = _secret_file.read_text().strip()
    else:
        _default_secret = _secrets.token_urlsafe(50)
        _secret_file.write_text(_default_secret)
SECRET_KEY = _default_secret

DEBUG = os.getenv('DEBUG', '1') == '1'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

if DEBUG:
    ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'corsheaders',
    'django_filters',

    # Project apps
    'core',
    'wanted',
    'soulseek',
    'traxdb',
    'recognize',
    'organize',
    'dig',
    'library',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'djtools_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'djtools_project.wsgi.application'

# PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'djtools'),
        'USER': os.getenv('POSTGRES_USER', 'djtools_user'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'djtools_password'),
        'HOST': os.getenv('POSTGRES_HOST', 'localhost'),
        'PORT': os.getenv('POSTGRES_PORT', '5433'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# DRF
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}

# CORS
CORS_ALLOWED_ORIGINS = os.getenv(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:5174,http://127.0.0.1:5174'
).split(',')
CORS_ALLOW_CREDENTIALS = True

# Chrome/Safari extensions use chrome-extension:// origins with a per-install ID
# that we can't know ahead of time. Allow them via regex so the extension works
# in DEBUG=False too, not only because CORS_ALLOW_ALL_ORIGINS is on.
CORS_ALLOWED_ORIGIN_REGEXES = [
    r'^chrome-extension://[a-p]{32}$',
    r'^moz-extension://[0-9a-f-]+$',
    r'^safari-web-extension://[0-9A-Fa-f-]+$',
]

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True

# ── DJ Tools Config ──────────────────────────────────────────
# slskd
SLSKD_BASE_URL = os.getenv('SLSKD_BASE_URL', 'http://localhost:5030')
SLSKD_API_KEY = os.getenv('SLSKD_API_KEY', '')

# AcoustID
ACOUSTID_API_KEY = os.getenv('ACOUSTID_API_KEY', '')

# Music paths
MUSIC_ROOT = os.getenv('MUSIC_ROOT', '/music')
SOULSEEK_DOWNLOAD_ROOT = os.getenv('SOULSEEK_DOWNLOAD_ROOT', '/music/soulseek')

# Spotify OAuth
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', '')
SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI', 'http://localhost:8002/api/wanted/import/spotify/callback/')

# Discogs
DISCOGS_PERSONAL_TOKEN = os.getenv('DISCOGS_PERSONAL_TOKEN', '')
DISCOGS_USERNAME = os.getenv('DISCOGS_USERNAME', '')

# YouTube Data API
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY', '')

# SoundCloud API
SC_CLIENT_ID = os.getenv('SC_CLIENT_ID', '')
SC_CLIENT_SECRET = os.getenv('SC_CLIENT_SECRET', '')
