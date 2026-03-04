from django.urls import path
from . import views

urlpatterns = [
    path('tracks/', views.track_list, name='library-tracks'),
    path('tracks/<int:pk>/', views.track_detail, name='library-track-detail'),
    path('tracks/<int:pk>/update/', views.track_update, name='library-track-update'),
    path('scan/', views.scan_library, name='library-scan'),
    path('scan/sync/', views.scan_library_sync, name='library-scan-sync'),
    path('stats/', views.library_stats, name='library-stats'),
]
