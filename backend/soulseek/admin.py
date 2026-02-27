from django.contrib import admin
from .models import SearchResult, Download, QualityPreset


@admin.register(SearchResult)
class SearchResultAdmin(admin.ModelAdmin):
    list_display = ['wanted_item', 'username', 'filename', 'match_score', 'file_extension', 'created']
    list_filter = ['file_extension']
    search_fields = ['filename', 'username']


@admin.register(Download)
class DownloadAdmin(admin.ModelAdmin):
    list_display = ['filename', 'username', 'status', 'progress', 'started', 'completed_at']
    list_filter = ['status']
    search_fields = ['filename', 'username']


@admin.register(QualityPreset)
class QualityPresetAdmin(admin.ModelAdmin):
    list_display = ['name', 'preferred_formats', 'min_bitrate', 'is_default']
