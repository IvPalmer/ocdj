from django.contrib import admin

from .models import AlbumIdentification, IdentifiedRelease, RecognitionRun


@admin.register(AlbumIdentification)
class AlbumIdentificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'artist_guess', 'album_guess', 'method', 'confidence', 'created']
    list_filter = ['method']
    search_fields = ['artist_guess', 'album_guess', 'image_hash']
    readonly_fields = ['image_hash', 'created']


@admin.register(IdentifiedRelease)
class IdentifiedReleaseAdmin(admin.ModelAdmin):
    list_display = ['id', 'artist', 'album', 'release_date', 'created']
    search_fields = ['artist', 'album']
    readonly_fields = ['created', 'updated']


@admin.register(RecognitionRun)
class RecognitionRunAdmin(admin.ModelAdmin):
    list_display = ['id', 'status', 'duration_ms', 'created']
    list_filter = ['status']
    readonly_fields = ['created', 'updated']
