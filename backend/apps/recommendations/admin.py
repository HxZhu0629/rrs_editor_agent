from django.contrib import admin

from .models import RecommendationItem, RecommendationRun

admin.site.register(RecommendationRun)
admin.site.register(RecommendationItem)
