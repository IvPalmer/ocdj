from rest_framework import serializers


class DigAddSerializer(serializers.Serializer):
    artist = serializers.CharField(required=False, allow_blank=True, default='')
    title = serializers.CharField(required=False, allow_blank=True, default='')
    release_name = serializers.CharField(required=False, allow_blank=True, default='')
    catalog_number = serializers.CharField(required=False, allow_blank=True, default='')
    label = serializers.CharField(required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    source_url = serializers.CharField(required=False, allow_blank=True, default='')
    source_site = serializers.ChoiceField(
        choices=['discogs', 'bandcamp', 'youtube', 'soundcloud', 'spotify'],
        required=True,
    )

    def validate(self, data):
        if not data.get('artist') and not data.get('title'):
            raise serializers.ValidationError('At least artist or title is required.')
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
