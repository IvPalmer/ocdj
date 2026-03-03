from django.urls import path
from . import views

urlpatterns = [
    path('jobs/', views.job_list, name='recognize-job-list'),
    path('jobs/<int:pk>/', views.job_detail, name='recognize-job-detail'),
    path('jobs/create/', views.create_job, name='recognize-create-job'),
    path('jobs/<int:pk>/add-to-wanted/', views.add_to_wanted, name='recognize-add-to-wanted'),
    path('jobs/<int:pk>/resume/', views.resume_job, name='recognize-resume-job'),
    path('jobs/<int:pk>/rerun/', views.rerun_job, name='recognize-rerun-job'),
    path('jobs/<int:pk>/delete/', views.delete_job, name='recognize-delete-job'),
    path('jobs/<int:pk>/recluster/', views.recluster_job, name='recognize-recluster-job'),
    path('trackid/lookup/', views.trackid_lookup, name='recognize-trackid-lookup'),
    path('acrcloud-usage/', views.acrcloud_usage, name='recognize-acrcloud-usage'),
]
