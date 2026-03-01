from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'sources', views.WantedSourceViewSet, basename='wanted-source')
router.register(r'items', views.WantedItemViewSet, basename='wanted-item')

urlpatterns = [
    path('', include(router.urls)),
    # Import operations
    path('import/operations/', views.import_operations, name='import-operations'),
    path('import/operations/<int:pk>/', views.import_operation_detail, name='import-operation-detail'),
    path('import/trigger/', views.trigger_import, name='import-trigger'),
    path('import/operations/<int:pk>/confirm/', views.confirm_import, name='import-confirm'),
    path('import/config-status/', views.import_config_status, name='import-config-status'),
    # Spotify OAuth
    path('import/spotify/auth/', views.spotify_auth_url, name='spotify-auth-url'),
    path('import/spotify/callback/', views.spotify_callback, name='spotify-callback'),
    path('import/spotify/status/', views.spotify_status, name='spotify-status'),
]
