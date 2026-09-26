from django.urls import path

from .views import ManuscriptConfirmView, ManuscriptDetailView, ManuscriptListView, ManuscriptUploadView

urlpatterns = [
    path('', ManuscriptListView.as_view(), name='manuscript-list'),
    path('upload/', ManuscriptUploadView.as_view(), name='manuscript-upload'),
    path('<uuid:manuscript_id>/', ManuscriptDetailView.as_view(), name='manuscript-detail'),
    path('<uuid:manuscript_id>/confirm/', ManuscriptConfirmView.as_view(), name='manuscript-confirm'),
]
