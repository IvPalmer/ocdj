from rest_framework import serializers
from .models import WantedSource, WantedItem, ImportOperation


class WantedSourceSerializer(serializers.ModelSerializer):
    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = WantedSource
        fields = [
            'id', 'name', 'url', 'source_type', 'last_checked',
            'active', 'created', 'item_count',
        ]
        read_only_fields = ['created']


class WantedItemSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source='source.name', read_only=True, default=None)
    search_results_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = WantedItem
        fields = [
            'id', 'artist', 'title', 'release_name', 'catalog_number', 'label',
            'source', 'source_name', 'notes',
            'status', 'identified_via', 'acoustid_fingerprint',
            'file_path', 'error_message', 'search_count',
            'search_results_count',
            'last_searched', 'best_match_score', 'added', 'updated',
        ]
        read_only_fields = ['added', 'updated']


class BulkAddSerializer(serializers.Serializer):
    """For adding multiple wanted items at once (e.g., from a tracklist)."""
    items = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
    )
    source_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_items(self, value):
        for item in value:
            if not item.get('artist') and not item.get('title'):
                raise serializers.ValidationError(
                    "Each item must have at least an artist or title."
                )
        return value


class ImportOperationSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source='source.name', read_only=True, default=None)

    class Meta:
        model = ImportOperation
        fields = [
            'id', 'import_type', 'status', 'url', 'playlist_name',
            'source', 'source_name',
            'preview_data', 'summary', 'total_found', 'duplicates_found',
            'items_imported', 'error_message', 'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


class ImportOperationListSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(source='source.name', read_only=True, default=None)

    class Meta:
        model = ImportOperation
        fields = [
            'id', 'import_type', 'status', 'url', 'playlist_name',
            'source', 'source_name',
            'total_found', 'duplicates_found', 'items_imported',
            'error_message', 'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


class TriggerImportSerializer(serializers.Serializer):
    import_type = serializers.ChoiceField(choices=['youtube', 'soundcloud', 'spotify', 'discogs', 'bandcamp'])
    url = serializers.URLField(required=False, allow_blank=True)

    def validate(self, data):
        import_type = data.get('import_type')
        url = data.get('url', '')
        if import_type in ('youtube', 'soundcloud', 'spotify', 'bandcamp') and not url:
            raise serializers.ValidationError({'url': 'URL is required for this import type.'})
        return data


class ConfirmImportSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=serializers.IntegerField(min_value=0),
        min_length=1,
        help_text='List of indices from preview_data to import',
    )
