from rest_framework import serializers
from .models import TraxDBOperation, ScrapedFolder, ScrapedTrack


class TraxDBOperationSerializer(serializers.ModelSerializer):
    """Full op including summary JSON. Use for detail endpoints only."""
    class Meta:
        model = TraxDBOperation
        fields = '__all__'
        read_only_fields = ['id', 'created', 'updated']


class TraxDBOperationListSerializer(serializers.ModelSerializer):
    """Lean op for list endpoints — omits summary JSON which can be 100KB+."""
    class Meta:
        model = TraxDBOperation
        fields = [
            'id', 'op_type', 'status', 'report_path', 'progress_path',
            'error_message', 'created', 'updated',
        ]
        read_only_fields = fields


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
    # Populated by the view via .annotate(...) — see folders_list. Falls back
    # to a live count if the annotation is missing (single-folder details).
    tracks_count = serializers.SerializerMethodField()
    tracks_downloaded = serializers.SerializerMethodField()
    # `download_status` alone can't answer "is this actually moving?". A list
    # sits in 'downloading' from the moment the Mac leases it — the daemon
    # leases a batch and works it one at a time — and stays there if the daemon
    # dies mid-list. `queue_state` is the distinction the panel needs.
    queue_state = serializers.SerializerMethodField()

    class Meta:
        model = ScrapedFolder
        fields = [
            'id', 'folder_id', 'title', 'url', 'pixeldrain_url',
            'inferred_date', 'scraped_at', 'download_status', 'queue_state',
            'tracks_count', 'tracks_downloaded', 'claimed_at',
            'last_error', 'last_error_at',
        ]

    def get_tracks_count(self, obj):
        annotated = getattr(obj, 'tracks_count_annotated', None)
        if annotated is not None:
            return annotated
        return obj.tracks.count()

    def get_tracks_downloaded(self, obj):
        annotated = getattr(obj, 'tracks_downloaded_annotated', None)
        if annotated is not None:
            return annotated
        return obj.tracks.filter(downloaded=True).count()

    def get_queue_state(self, obj):
        """What the panel should say this list is doing.

        Adds two distinctions the stored status can't make:
          * `stalled` — leased, but the lease has outlived LEASE_MINUTES, so
            nobody is working on it and it will be re-offered.
          * `blocked` — pending, but its date already exists on the Mac, so
            `local_claim` will never hand it out (a date folder is atomic).
            Counting these as "waiting" makes a queue that never drains.
        The view supplies `held_dates` and `lease_cutoff` via context; without
        them this degrades to the stored status rather than guessing.
        """
        status = obj.download_status
        if status == 'downloading':
            cutoff = self.context.get('lease_cutoff')
            if cutoff is not None and (obj.claimed_at is None or obj.claimed_at < cutoff):
                return 'stalled'
            return 'claimed'
        if status == 'pending':
            held = self.context.get('held_dates')
            if held is not None and obj.inferred_date and obj.inferred_date in held:
                return 'blocked'
            return 'waiting'
        return status


class ScrapedFolderDetailSerializer(ScrapedFolderSerializer):
    tracks = ScrapedTrackSerializer(many=True, read_only=True)

    class Meta(ScrapedFolderSerializer.Meta):
        fields = ScrapedFolderSerializer.Meta.fields + ['tracks', 'pixeldrain_links']
