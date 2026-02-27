from django.contrib import admin
from .models import WantedSource, WantedItem


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
