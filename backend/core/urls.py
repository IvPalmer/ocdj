from django.urls import path
from . import views

urlpatterns = [
    path('health/', views.health, name='health'),
    path('stats/', views.stats, name='stats'),
    path('config/', views.config_list, name='config-list'),
    path('config/update/', views.config_update, name='config-update'),
]
