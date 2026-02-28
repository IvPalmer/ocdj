from rest_framework import serializers
from .models import TraxDBOperation


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
