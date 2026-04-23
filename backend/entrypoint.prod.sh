#!/bin/sh
set -e

echo "[entrypoint] git_sha=${GIT_SHA:-unknown}"
echo "[entrypoint] applying migrations"
python manage.py migrate --noinput

echo "[entrypoint] starting gunicorn on 0.0.0.0:8002"
exec gunicorn \
  --bind 0.0.0.0:8002 \
  --workers ${GUNICORN_WORKERS:-3} \
  --timeout ${GUNICORN_TIMEOUT:-300} \
  --access-logfile - \
  --error-logfile - \
  djtools_project.wsgi:application
