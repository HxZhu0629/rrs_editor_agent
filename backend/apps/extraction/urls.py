from django.urls import path

from .views import ExtractionRunChunkListView, ExtractionRunVectorSearchView, ManuscriptExtractionView

urlpatterns = [
    path('manuscripts/<uuid:manuscript_id>/run/', ManuscriptExtractionView.as_view(), name='manuscript-extraction-run'),
    path('runs/<uuid:run_id>/chunks/', ExtractionRunChunkListView.as_view(), name='extraction-run-chunks'),
    path('runs/<uuid:run_id>/vector-search/', ExtractionRunVectorSearchView.as_view(), name='extraction-run-vector-search'),
]
