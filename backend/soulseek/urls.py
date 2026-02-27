from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'queue', views.SearchQueueViewSet, basename='search-queue')
router.register(r'presets', views.QualityPresetViewSet, basename='quality-preset')

urlpatterns = [
    path('health/', views.slskd_health, name='slskd-health'),
    path('search/', views.search, name='slskd-search'),
    path('search/results/', views.search_results, name='slskd-search-results'),
    path('search/recent/', views.recent_searches, name='slskd-recent-searches'),
    path('download/', views.download_file, name='slskd-download'),
    path('downloads/', views.downloads_status, name='slskd-downloads'),
    path('downloads/cancel/', views.cancel_download, name='slskd-cancel-download'),
    path('downloads/clear/', views.clear_downloads, name='slskd-clear-downloads'),
    path('', include(router.urls)),
]
