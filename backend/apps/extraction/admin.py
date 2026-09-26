from django.contrib import admin

from .models import DocumentChunk, ExtractionReview, ExtractionRun

admin.site.register(ExtractionRun)
admin.site.register(ExtractionReview)
admin.site.register(DocumentChunk)
