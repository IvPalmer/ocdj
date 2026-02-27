from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from .models import WantedSource, WantedItem
from .serializers import WantedSourceSerializer, WantedItemSerializer, BulkAddSerializer


class WantedSourceViewSet(viewsets.ModelViewSet):
    serializer_class = WantedSourceSerializer
    filterset_fields = ['source_type', 'active']
    search_fields = ['name']

    def get_queryset(self):
        return WantedSource.objects.annotate(
            item_count=Count('items')
        )


class WantedItemViewSet(viewsets.ModelViewSet):
    serializer_class = WantedItemSerializer

    def get_queryset(self):
        return WantedItem.objects.select_related('source').annotate(
            search_results_count=Count('search_results')
        )
    filterset_fields = ['status', 'source', 'identified_via']
    search_fields = ['artist', 'title', 'notes']
    ordering_fields = ['added', 'updated', 'artist', 'title', 'status']

    @action(detail=False, methods=['post'])
    def bulk_add(self, request):
        """Add multiple wanted items at once."""
        serializer = BulkAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        source_id = serializer.validated_data.get('source_id')
        items_data = serializer.validated_data['items']

        created = []
        for item_data in items_data:
            item = WantedItem.objects.create(
                artist=item_data.get('artist', ''),
                title=item_data.get('title', ''),
                notes=item_data.get('notes', ''),
                source_id=source_id,
            )
            created.append(item)

        return Response(
            WantedItemSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['post'])
    def bulk_update_status(self, request):
        """Update status for multiple items."""
        ids = request.data.get('ids', [])
        new_status = request.data.get('status')

        if not ids or not new_status:
            return Response(
                {'error': 'ids and status are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        valid_statuses = [c[0] for c in WantedItem.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {'error': f'Invalid status. Must be one of: {valid_statuses}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = WantedItem.objects.filter(id__in=ids).update(status=new_status)
        return Response({'updated': updated})

    @action(detail=False, methods=['delete'])
    def bulk_delete(self, request):
        """Delete multiple items."""
        ids = request.data.get('ids', [])
        if not ids:
            return Response(
                {'error': 'ids are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deleted, _ = WantedItem.objects.filter(id__in=ids).delete()
        return Response({'deleted': deleted})
