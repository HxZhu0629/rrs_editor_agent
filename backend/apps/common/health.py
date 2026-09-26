import secrets

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Min
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from redis import Redis

from .telemetry import metrics_payload


@require_GET
def live(request):
    return JsonResponse({'status': 'ok'})


@require_GET
def ready(request):
    dependencies = {}
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                if connection.vendor == 'postgresql':
                    cursor.execute("SET LOCAL statement_timeout = '1000ms'")
                cursor.execute('SELECT 1')
                cursor.fetchone()
        dependencies['database'] = 'ok'
    except Exception:
        dependencies['database'] = 'unavailable'
    try:
        with Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=1, socket_timeout=1) as client:
            client.ping()
        dependencies['broker'] = 'ok'
    except Exception:
        dependencies['broker'] = 'unavailable'
    healthy = all(value == 'ok' for value in dependencies.values())
    return JsonResponse({'status': 'ready' if healthy else 'unavailable', 'dependencies': dependencies}, status=200 if healthy else 503)


@require_GET
def metrics(request):
    token = settings.METRICS_TOKEN
    if not token or not secrets.compare_digest(request.headers.get('Authorization', '').encode(), f'Bearer {token}'.encode()):
        return HttpResponse(status=403)
    try:
        from apps.agent.models import AgentRun
        registry = CollectorRegistry()
        pending = Gauge('rrs_agent_active_runs', 'Active runs by state', ['state'], registry=registry)
        oldest = Gauge('rrs_agent_oldest_pending_seconds', 'Age of oldest processing run segment', registry=registry)
        rows = AgentRun.objects.filter(status__in=['processing', 'waiting_approval']).values('status').annotate(count=Count('id'), oldest=Min('queued_at'))
        pending.labels(state='processing').set(0)
        pending.labels(state='waiting_approval').set(0)
        oldest.set(0)
        for row in rows:
            pending.labels(state=row['status']).set(row['count'])
            if row['status'] == 'processing':
                oldest.set(max(0, (timezone.now() - row['oldest']).total_seconds()))
        return HttpResponse(metrics_payload() + generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
    except Exception:
        return HttpResponse(status=503)
