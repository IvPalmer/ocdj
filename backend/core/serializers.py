from rest_framework import serializers
from .models import Config


class ConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = Config
        fields = ['key', 'value', 'updated']
        read_only_fields = ['updated']
