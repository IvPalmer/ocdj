from rest_framework import serializers
from .models import RecognizeJob


class RecognizeJobSerializer(serializers.ModelSerializer):
    cleanup = serializers.SerializerMethodField()

    def get_cleanup(self, obj):
        from wanted.models import MetadataCleanup
        from wanted.serializers import MetadataCleanupSerializer
        return MetadataCleanupSerializer(MetadataCleanup.objects.filter(job_id=obj.pk).order_by('pk'), many=True).data

    class Meta:
        model = RecognizeJob
        fields = [
            'id', 'url', 'title', 'status',
            'segments_total', 'segments_done', 'tracks_found',
            'tracklist', 'raw_results', 'description_tracks', 'cleanup',
            'duration_seconds', 'engine', 'acrcloud_calls',
            'error_message', 'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


class RecognizeJobListSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecognizeJob
        fields = [
            'id', 'url', 'title', 'status',
            'segments_total', 'segments_done', 'tracks_found',
            'duration_seconds', 'engine', 'acrcloud_calls',
            'error_message', 'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


class CreateJobSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2000)

    def validate_url(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('URL is required.')
        return value
