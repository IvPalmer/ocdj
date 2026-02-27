from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'sources', views.WantedSourceViewSet, basename='wanted-source')
router.register(r'items', views.WantedItemViewSet, basename='wanted-item')

urlpatterns = [
    path('', include(router.urls)),
]
