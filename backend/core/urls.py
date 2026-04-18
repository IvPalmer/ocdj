from django.urls import path
from . import views

urlpatterns = [
    path('health/', views.health, name='health'),
    path('stats/', views.stats, name='stats'),
    path('config/', views.config_list, name='config-list'),
    path('config/schema/', views.config_schema, name='config-schema'),
    path('config/update/', views.config_update, name='config-update'),
    path('automation/run/', views.automation_run, name='automation-run'),
    path('automation/config/', views.automation_config, name='automation-config'),
    path('automation/status/', views.automation_status, name='automation-status'),
    path('audit-music-root/', views.audit_music_root, name='audit-music-root'),
]
