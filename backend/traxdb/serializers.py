from rest_framework import serializers
from .models import TraxDBOperation, ScrapedFolder, ScrapedTrack


class TraxDBOperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = TraxDBOperation
        fields = '__all__'
        read_only_fields = ['id', 'created', 'updated']


class TriggerSyncSerializer(serializers.Serializer):
    max_pages = serializers.IntegerField(default=50, min_value=1, max_value=500, required=False)


class TriggerDownloadSerializer(serializers.Serializer):
    sync_operation_id = serializers.IntegerField(required=False, help_text='ID of sync op to use. Defaults to latest completed sync.')
    links_key = serializers.ChoiceField(choices=['links_found', 'links_new'], default='links_new', required=False)


class TriggerAuditSerializer(serializers.Serializer):
    sync_operation_id = serializers.IntegerField(required=False, help_text='ID of sync op to use. Defaults to latest completed sync.')


class ScrapedTrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapedTrack
        fields = [
            'id', 'filename', 'pixeldrain_file_id', 'pixeldrain_url',
            'local_path', 'file_size_bytes', 'downloaded', 'download_status',
        ]


class ScrapedFolderSerializer(serializers.ModelSerializer):
    tracks_count = serializers.SerializerMethodField()
    tracks_downloaded = serializers.SerializerMethodField()

    class Meta:
        model = ScrapedFolder
        fields = [
            'id', 'folder_id', 'title', 'url', 'pixeldrain_url',
            'inferred_date', 'scraped_at', 'download_status',
            'tracks_count', 'tracks_downloaded',
        ]

    def get_tracks_count(self, obj):
        return obj.tracks.count()

    def get_tracks_downloaded(self, obj):
        return obj.tracks.filter(downloaded=True).count()


class ScrapedFolderDetailSerializer(ScrapedFolderSerializer):
    tracks = ScrapedTrackSerializer(many=True, read_only=True)

    class Meta(ScrapedFolderSerializer.Meta):
        fields = ScrapedFolderSerializer.Meta.fields + ['tracks', 'pixeldrain_links']
