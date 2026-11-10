from rest_framework import serializers
from .models import PipelineItem


class PipelineItemSerializer(serializers.ModelSerializer):
    playback_url = serializers.SerializerMethodField()
    playback_unavailable = serializers.SerializerMethodField()
    archive_library_match = serializers.SerializerMethodField()

    def get_archive_library_match(self, item):
        if item.archive_state != 'archived':
            return None
        from library.matching import match_result
        from library.models import LibraryTrack

        rows = list(LibraryTrack.objects.filter(source='mac_snapshot', missing=False, archive_item_id=item.pk)
                    .order_by('pk').values('id', 'format', 'bitrate', 'sample_rate', 'source', 'file_path')[:4])
        return match_result(rows) if rows else None

    def _playback(self, item):
        from django.urls import reverse
        from .services.playback import open_audio, PlaybackUnavailable
        # Both fields share one availability check per serialized row.
        if not hasattr(self, '_playback_results'):
            self._playback_results = {}
        if item.pk not in self._playback_results:
            try:
                handle, _, _ = open_audio(item)
                handle.close()
                result = (reverse('pipeline-audio', args=[item.pk]), '')
            except PlaybackUnavailable as exc:
                result = (None, str(exc))
            self._playback_results[item.pk] = result
        return self._playback_results[item.pk]

    def get_playback_url(self, item):
        return self._playback(item)[0]

    def get_playback_unavailable(self, item):
        return self._playback(item)[1]

    class Meta:
        model = PipelineItem
        fields = [
            'id', 'download', 'wanted_item', 'playback_url', 'playback_unavailable', 'archive_library_match',
            'original_filename', 'current_path', 'final_filename',
            'artist', 'title', 'album', 'label', 'catalog_number',
            'genre', 'year', 'track_number', 'has_artwork',
            'stage', 'error_message', 'metadata_source', 'retry_after',
            'archive_state', 'sha256', 'work_path',
            'music_persistent_id', 'published_at', 'archived_at',
            'drain_attempts',
            'created', 'updated',
        ]
        read_only_fields = ['created', 'updated']


# PipelineStatsSerializer used to live here. It was never referenced and it
# re-declared the stage list a third time (model, view, serializer) — the same
# drift that dropped 'published' from the stats response. pipeline_stats now
# derives its keys from PipelineItem.STAGE_CHOICES directly.
