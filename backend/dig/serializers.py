from rest_framework import serializers


class DigAddSerializer(serializers.Serializer):
    artist = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    title = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    release_name = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    catalog_number = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    label = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    isrc = serializers.RegexField(r'^[A-Za-z]{2}[A-Za-z0-9]{3}[0-9]{7}$',
                                  required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    source_url = serializers.CharField(required=False, allow_blank=True, default='')
    source_site = serializers.ChoiceField(
        choices=['discogs', 'bandcamp', 'youtube', 'soundcloud', 'spotify', 'beatport'],
        required=True,
    )

    def validate(self, data):
        if not any(data.get(field) for field in ('artist', 'title', 'release_name')):
            raise serializers.ValidationError('An artist, track title, or release is required.')
        return data


class DigBatchSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
    )
    source_url = serializers.CharField(required=False, allow_blank=True, default='')
    source_site = serializers.ChoiceField(
        choices=['discogs', 'bandcamp', 'youtube', 'soundcloud', 'spotify'],
        required=True,
    )
    skip_duplicates = serializers.BooleanField(default=True)

    def validate_items(self, value):
        for item in value:
            if not item.get('artist') and not item.get('title'):
                raise serializers.ValidationError(
                    'Each item must have at least an artist or title.'
                )
        return value


class DigCheckSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
    )
