from django.urls import path
from . import views

urlpatterns = [
    path('inventory/', views.inventory),
    path('operations/', views.operations),
    path('operations/<int:pk>/', views.operation_detail),
    path('sync/', views.trigger_sync),
    path('download/', views.trigger_download),
    path('download/<int:pk>/progress/', views.download_progress),
    path('download/<int:pk>/cancel/', views.cancel_download),
    path('audit/', views.trigger_audit),
]
