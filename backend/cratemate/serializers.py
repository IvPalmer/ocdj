"""Cratemate DRF serializers — port of crate-mate's FastAPI/pydantic responses."""
from rest_framework import serializers

from .models import AlbumIdentification, IdentifiedRelease, RecognitionRun


class IdentifiedReleaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = IdentifiedRelease
        fields = [
            'id', 'artist', 'album', 'release_date', 'genres', 'cover_image_url',
            'discogs_url', 'spotify_url', 'youtube_url', 'bandcamp_url',
            'tracklist', 'market_stats', 'extra',
            'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


class AlbumIdentificationSerializer(serializers.ModelSerializer):
    releases = IdentifiedReleaseSerializer(many=True, read_only=True)

    class Meta:
        model = AlbumIdentification
        fields = [
            'id', 'image_hash', 'method', 'raw_response',
            'artist_guess', 'album_guess', 'confidence',
            'error_message', 'created', 'releases',
        ]
        read_only_fields = ['created']


class RecognitionRunSerializer(serializers.ModelSerializer):
    identification = AlbumIdentificationSerializer(read_only=True)
    release = IdentifiedReleaseSerializer(read_only=True)

    class Meta:
        model = RecognitionRun
        fields = [
            'id', 'status', 'duration_ms', 'sources_used',
            'error_message', 'identification', 'release',
            'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


class IdentifyRequestSerializer(serializers.Serializer):
    """POST /api/cratemate/identify/ — multipart form with `image`."""
    image = serializers.ImageField()


class ManualLookupSerializer(serializers.Serializer):
    """POST /api/cratemate/lookup/ — fallback when image identification fails."""
    artist = serializers.CharField(max_length=500)
    album = serializers.CharField(max_length=500)
    sources = serializers.ListField(
        child=serializers.CharField(max_length=50),
        required=False,
        default=list,
    )
