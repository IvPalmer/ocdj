from django.urls import path

from . import views

urlpatterns = [
    path('health/', views.drain_health, name='drain-health'),
    path('publishable/', views.drain_publishable, name='drain-publishable'),
    path('<int:pk>/confirm/', views.drain_confirm, name='drain-confirm'),
    path('<int:pk>/fail/', views.drain_fail, name='drain-fail'),
]
