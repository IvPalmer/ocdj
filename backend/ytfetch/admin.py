from django.contrib import admin

from .models import FetchJob


@admin.register(FetchJob)
class FetchJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'uploader', 'status', 'created')
    list_filter = ('status',)
    search_fields = ('url', 'video_id', 'title', 'uploader')
