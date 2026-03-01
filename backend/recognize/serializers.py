from rest_framework import serializers
from .models import RecognizeJob


class RecognizeJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecognizeJob
        fields = [
            'id', 'url', 'title', 'status',
            'segments_total', 'segments_done', 'tracks_found',
            'tracklist', 'description_tracks',
            'duration_seconds', 'error_message',
            'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


class RecognizeJobListSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecognizeJob
        fields = [
            'id', 'url', 'title', 'status',
            'segments_total', 'segments_done', 'tracks_found',
            'duration_seconds', 'error_message',
            'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


class CreateJobSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2000)

    def validate_url(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('URL is required.')
        return value
