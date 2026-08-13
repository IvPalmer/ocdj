from django.urls import path
from . import views, views_local

urlpatterns = [
    # Mac local-download daemon (bearer-token authed, see views_local)
    path('local/inventory/', views_local.local_inventory),
    path('local/claim/', views_local.local_claim),
    path('local/<int:pk>/complete/', views_local.local_complete),
    path('local/<int:pk>/fail/', views_local.local_fail),

    path('inventory/', views.inventory),
    path('operations/', views.operations),
    path('operations/<int:pk>/', views.operation_detail),
    path('sync/', views.trigger_sync),
    path('download/', views.trigger_download),
    path('download/<int:pk>/progress/', views.download_progress),
    path('download/<int:pk>/cancel/', views.cancel_download),
    path('audit/', views.trigger_audit),
    # Scraped folders/tracks browsing
    path('folders/', views.folders_list),
    path('folders/<int:pk>/', views.folder_detail),
    path('folders/<int:pk>/tracks/', views.folder_tracks),
]
