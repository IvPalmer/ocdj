from django.urls import path
from . import views

urlpatterns = [
    path('fetch/', views.fetch),
    path('jobs/', views.jobs),
    path('jobs/<int:pk>/retry/', views.retry_job),
    path('jobs/<int:pk>/', views.delete_job),
    path('worker/claim/', views.worker_claim),
    path('worker/jobs/<int:pk>/complete/', views.worker_complete),
    path('worker/jobs/<int:pk>/fail/', views.worker_fail),
]
