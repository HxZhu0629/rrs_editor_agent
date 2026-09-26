from django.urls import path

from .views import RecommendationRunDetailView, RecommendationRunView, ReviewerPaperAccessView

urlpatterns = [
    path('run/', RecommendationRunView.as_view(), name='recommendation-run'),
    path('runs/<uuid:run_id>/', RecommendationRunDetailView.as_view(), name='recommendation-run-detail'),
    path('papers/<uuid:paper_id>/access/', ReviewerPaperAccessView.as_view(), name='reviewer-paper-access'),
]
