from django.contrib import admin

from .models import ReviewerCoauthorEdge, ReviewerPaper, ReviewerProfile

admin.site.register(ReviewerProfile)
admin.site.register(ReviewerPaper)
admin.site.register(ReviewerCoauthorEdge)
