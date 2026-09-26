#!/bin/sh
set -e
export PROMETHEUS_MULTIPROC_DIR="$(mktemp -d /tmp/rrs-web-metrics.XXXXXX)"

python manage.py migrate --noinput
python manage.py collectstatic --noinput || true

exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-3}" --timeout 120
