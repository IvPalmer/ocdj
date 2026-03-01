from django.contrib import admin
from .models import PipelineItem


@admin.register(PipelineItem)
class PipelineItemAdmin(admin.ModelAdmin):
    list_display = ['original_filename', 'artist', 'title', 'stage', 'metadata_source', 'created']
    list_filter = ['stage', 'metadata_source']
    search_fields = ['original_filename', 'artist', 'title', 'album']
