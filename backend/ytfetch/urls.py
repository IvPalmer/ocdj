from django.urls import path
from . import views

urlpatterns = [
    path('fetch/', views.fetch),
    path('jobs/', views.jobs),
    path('jobs/<int:pk>/retry/', views.retry_job),
    path('jobs/<int:pk>/', views.delete_job),
]
