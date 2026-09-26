from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ReviewerProfileViewSet

router = DefaultRouter()
router.register(r'', ReviewerProfileViewSet, basename='reviewer')

urlpatterns = [
    path('', include(router.urls)),
]
