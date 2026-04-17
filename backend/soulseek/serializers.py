from rest_framework import serializers
from .models import SearchQueueItem, SearchResult, Download, QualityPreset


class SearchQueueItemSerializer(serializers.ModelSerializer):
    # Populated by the viewset via .annotate(search_results_count=Count(...))
    # to avoid N+1 queries when listing the queue. Falls back to a live count
    # if the annotation is missing (e.g. single-item retrieves).
    search_results_count = serializers.SerializerMethodField()
    display_label = serializers.CharField(read_only=True)

    class Meta:
        model = SearchQueueItem
        fields = [
            'id', 'wanted_item', 'artist', 'title', 'release_name',
            'catalog_number', 'label', 'raw_query', 'status',
            'search_count', 'last_searched', 'best_match_score',
            'error_message', 'display_label', 'search_results_count',
            'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']

    def get_search_results_count(self, obj):
        annotated = getattr(obj, 'search_results_count_annotated', None)
        if annotated is not None:
            return annotated
        return obj.search_results.count()


class AddToQueueSerializer(serializers.Serializer):
    """For adding items to the search queue — from wanted items or free-text."""
    wanted_item_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False
    )
    query = serializers.CharField(required=False)

    def validate(self, data):
        if not data.get('wanted_item_ids') and not data.get('query'):
            raise serializers.ValidationError(
                "Either wanted_item_ids or query is required."
            )
        return data


class SearchResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = SearchResult
        fields = '__all__'
        read_only_fields = ['created']


class DownloadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Download
        fields = '__all__'
        read_only_fields = ['started']


class QualityPresetSerializer(serializers.ModelSerializer):
    class Meta:
        model = QualityPreset
        fields = '__all__'


class SearchRequestSerializer(serializers.Serializer):
    """For triggering a search."""
    queue_item_id = serializers.IntegerField(required=False)
    wanted_item_id = serializers.IntegerField(required=False)  # backward compat
    query = serializers.CharField(required=False)

    def validate(self, data):
        if not data.get('queue_item_id') and not data.get('wanted_item_id') and not data.get('query'):
            raise serializers.ValidationError(
                "One of queue_item_id, wanted_item_id, or query is required."
            )
        return data


class DownloadRequestSerializer(serializers.Serializer):
    """For triggering a download from search results."""
    username = serializers.CharField()
    filename = serializers.CharField()
    size = serializers.IntegerField(required=False, default=0)
    queue_item_id = serializers.IntegerField(required=False)
    wanted_item_id = serializers.IntegerField(required=False)  # backward compat
