#!/bin/sh
set -e
export PROMETHEUS_MULTIPROC_DIR="$(mktemp -d /tmp/rrs-worker-metrics.XXXXXX)"
exec celery -A config worker --loglevel=info --concurrency="${WORKER_CONCURRENCY:-2}"
