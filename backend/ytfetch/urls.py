from django.urls import path
from . import views

urlpatterns = [
    path('fetch/', views.fetch),
    path('jobs/', views.jobs),
    path('jobs/<int:pk>/retry/', views.retry_job),
    path('jobs/<int:pk>/', views.delete_job),
    path('pending-local/', views.pending_local),
    path('<int:pk>/meta/', views.meta_local),
    path('<int:pk>/deliver-local/', views.deliver_local),
]
