from django.contrib import admin
from .models import TraxDBOperation, ScrapedFolder, ScrapedTrack


@admin.register(TraxDBOperation)
class TraxDBOperationAdmin(admin.ModelAdmin):
    list_display = ['op_type', 'status', 'created', 'updated']
    list_filter = ['op_type', 'status']
    readonly_fields = ['summary', 'report_path', 'progress_path']


class ScrapedTrackInline(admin.TabularInline):
    model = ScrapedTrack
    extra = 0
    readonly_fields = ['filename', 'pixeldrain_file_id', 'file_size_bytes', 'downloaded', 'download_status', 'local_path']


@admin.register(ScrapedFolder)
class ScrapedFolderAdmin(admin.ModelAdmin):
    list_display = ['folder_id', 'inferred_date', 'download_status', 'scraped_at']
    list_filter = ['download_status']
    search_fields = ['folder_id', 'title']
    readonly_fields = ['scraped_at']
    inlines = [ScrapedTrackInline]


@admin.register(ScrapedTrack)
class ScrapedTrackAdmin(admin.ModelAdmin):
    list_display = ['filename', 'folder', 'downloaded', 'download_status', 'file_size_bytes']
    list_filter = ['downloaded', 'download_status']
    search_fields = ['filename']
