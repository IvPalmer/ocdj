from django.urls import path
from . import views

urlpatterns = [
    path('pipeline/', views.pipeline_list, name='pipeline-list'),
    path('pipeline/stats/', views.pipeline_stats, name='pipeline-stats'),
    path('pipeline/process/', views.pipeline_process_all, name='pipeline-process-all'),
    path('pipeline/scan/', views.pipeline_scan, name='pipeline-scan'),
    path('pipeline/<int:pk>/', views.pipeline_detail, name='pipeline-detail'),
    path('pipeline/<int:pk>/process/', views.pipeline_process_single, name='pipeline-process-single'),
    path('pipeline/<int:pk>/retry/', views.pipeline_retry, name='pipeline-retry'),
    path('pipeline/<int:pk>/skip/', views.pipeline_skip, name='pipeline-skip'),
    path('pipeline/<int:pk>/retag/', views.pipeline_retag, name='pipeline-retag'),
]
