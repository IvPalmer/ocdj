from django.urls import path
from . import views
urlpatterns = [
    path('', views.workspace),
    path('media/<int:pk>/', views.media_action),
    path('media/<int:pk>/candidate-preview/', views.candidate_preview),
    path('media/<int:pk>/<str:action>/', views.media_action),
    path('connector/heartbeat/', views.heartbeat),
    path('connector/sync/', views.sync),
    path('connector/claim/', views.claim),
    path('connector/media/<int:pk>/complete/', views.complete),
    path('connector/media/<int:pk>/upload/', views.upload),
]
# Specific streaming route must precede generic action.
urlpatterns.insert(1, path('media/<int:pk>/stream/', views.stream))
