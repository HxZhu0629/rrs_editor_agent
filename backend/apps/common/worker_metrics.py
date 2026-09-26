import secrets
from threading import Thread
from wsgiref.simple_server import WSGIRequestHandler, make_server

from celery.signals import worker_ready
from django.conf import settings
from prometheus_client import CONTENT_TYPE_LATEST

from .telemetry import event, metrics_payload


def application(environ, start_response):
    if environ.get('PATH_INFO') != '/metrics':
        start_response('404 Not Found', [])
        return [b'']
    if not settings.METRICS_TOKEN or not secrets.compare_digest(environ.get('HTTP_AUTHORIZATION', '').encode(), f'Bearer {settings.METRICS_TOKEN}'.encode()):
        start_response('403 Forbidden', [])
        return [b'']
    try:
        payload = metrics_payload()
    except Exception:
        start_response('503 Service Unavailable', [])
        return [b'']
    start_response('200 OK', [('Content-Type', CONTENT_TYPE_LATEST)])
    return [payload]


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


@worker_ready.connect
def start_worker_metrics(**kwargs):
    if not settings.WORKER_METRICS_PORT:
        return
    try:
        server = make_server('0.0.0.0', settings.WORKER_METRICS_PORT, application, handler_class=QuietHandler)
        Thread(target=server.serve_forever, daemon=True, name='worker-metrics').start()
    except Exception as exc:
        event('worker_metrics_unavailable', error_type=type(exc).__name__)
