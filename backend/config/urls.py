from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from apps.common import health

urlpatterns = [
    path('health/live/', health.live, name='health-live'),
    path('health/ready/', health.ready, name='health-ready'),
    path('metrics', health.metrics, name='metrics'),
    path('admin/', admin.site.urls),
    path('api/manuscripts/', include('apps.manuscripts.urls')),
    path('api/extraction/', include('apps.extraction.urls')),
    path('api/reviewers/', include('apps.reviewers.urls')),
    path('api/recommendations/', include('apps.recommendations.urls')),
    path('api/agent/', include('apps.agent.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
