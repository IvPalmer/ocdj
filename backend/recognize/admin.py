from django.contrib import admin
from .models import RecognizeJob


@admin.register(RecognizeJob)
class RecognizeJobAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'status', 'tracks_found', 'created']
    list_filter = ['status']
    search_fields = ['title', 'url']
