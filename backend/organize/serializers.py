from rest_framework import serializers
from .models import PipelineItem


class PipelineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineItem
        fields = [
            'id', 'download', 'wanted_item',
            'original_filename', 'current_path', 'final_filename',
            'artist', 'title', 'album', 'label', 'catalog_number',
            'genre', 'year', 'track_number', 'has_artwork',
            'stage', 'error_message', 'metadata_source',
            'archive_state', 'sha256', 'work_path',
            'music_persistent_id', 'published_at', 'archived_at',
            'drain_attempts',
            'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


class PipelineStatsSerializer(serializers.Serializer):
    downloaded = serializers.IntegerField()
    tagging = serializers.IntegerField()
    tagged = serializers.IntegerField()
    renaming = serializers.IntegerField()
    renamed = serializers.IntegerField()
    converting = serializers.IntegerField()
    converted = serializers.IntegerField()
    ready = serializers.IntegerField()
    failed = serializers.IntegerField()
    total = serializers.IntegerField()
