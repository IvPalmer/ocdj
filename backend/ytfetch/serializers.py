import re

from rest_framework import serializers

from .models import FetchJob


# youtube.com/watch?v=..., youtu.be/..., youtube.com/shorts/..., and the
# live/embed variants. Kept deliberately narrow so junk URLs are rejected up
# front rather than wasting a yt-dlp run.
_YOUTUBE_URL_RE = re.compile(
    r'^https?://'
    r'(?:www\.|m\.)?'
    r'(?:'
    r'youtube\.com/(?:watch\?|shorts/|live/|embed/)'
    r'|youtu\.be/'
    r')',
    re.IGNORECASE,
)


def is_youtube_url(value):
    return bool(_YOUTUBE_URL_RE.match((value or '').strip()))


class FetchJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = FetchJob
        fields = [
            'id', 'url', 'video_id', 'title', 'uploader', 'status',
            'error_message', 'downloaded_path', 'pipeline_item',
            'created', 'updated',
        ]
        read_only_fields = fields


class CreateFetchSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2000)

    def validate_url(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('URL is required.')
        if not is_youtube_url(value):
            raise serializers.ValidationError('Not a recognized YouTube URL.')
        return value
