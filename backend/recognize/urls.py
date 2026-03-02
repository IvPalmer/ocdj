from django.urls import path
from . import views

urlpatterns = [
    path('jobs/', views.job_list, name='recognize-job-list'),
    path('jobs/<int:pk>/', views.job_detail, name='recognize-job-detail'),
    path('jobs/create/', views.create_job, name='recognize-create-job'),
    path('jobs/<int:pk>/add-to-wanted/', views.add_to_wanted, name='recognize-add-to-wanted'),
    path('trackid/lookup/', views.trackid_lookup, name='recognize-trackid-lookup'),
]
