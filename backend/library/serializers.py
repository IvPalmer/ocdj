from rest_framework import serializers
from .models import LibraryTrack


class LibraryTrackSerializer(serializers.ModelSerializer):
    filename = serializers.SerializerMethodField()

    class Meta:
        model = LibraryTrack
        fields = [
            'id', 'file_path', 'filename',
            'artist', 'title', 'album', 'label', 'catalog_number',
            'genre', 'year',
            'format', 'bitrate', 'sample_rate', 'duration_seconds',
            'file_size_bytes', 'has_artwork',
            'source', 'missing',
            'added_at', 'updated_at',
        ]
        read_only_fields = ['added_at', 'updated_at', 'file_path', 'filename']

    def get_filename(self, obj):
        return obj.file_path.split('/')[-1] if obj.file_path else ''


class LibraryTrackUpdateSerializer(serializers.Serializer):
    artist = serializers.CharField(required=False, allow_blank=True)
    title = serializers.CharField(required=False, allow_blank=True)
    album = serializers.CharField(required=False, allow_blank=True)
    label = serializers.CharField(required=False, allow_blank=True)
    catalog_number = serializers.CharField(required=False, allow_blank=True)
    genre = serializers.CharField(required=False, allow_blank=True)
    year = serializers.CharField(required=False, allow_blank=True)
