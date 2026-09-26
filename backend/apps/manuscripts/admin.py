from django.contrib import admin

from .models import Affiliation, Author, Manuscript, ManuscriptAuthor

admin.site.register(Manuscript)
admin.site.register(Author)
admin.site.register(Affiliation)
admin.site.register(ManuscriptAuthor)
