from django.contrib import admin
from .models import WantedSource, WantedItem, MetadataCleanup


@admin.register(MetadataCleanup)
class MetadataCleanupAdmin(admin.ModelAdmin):
    list_display = ['subject_key', 'status', 'attempts', 'created', 'updated']
    list_filter = ['status']
    readonly_fields = [field.name for field in MetadataCleanup._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WantedSource)
class WantedSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'source_type', 'active', 'last_checked', 'created']
    list_filter = ['source_type', 'active']
    search_fields = ['name']


@admin.register(WantedItem)
class WantedItemAdmin(admin.ModelAdmin):
    list_display = ['artist', 'title', 'source', 'status', 'added', 'updated']
    list_filter = ['status', 'source', 'identified_via']
    search_fields = ['artist', 'title', 'notes']
    readonly_fields = ['added', 'updated']
