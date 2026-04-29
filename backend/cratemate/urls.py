from django.urls import path

from . import views

urlpatterns = [
    path('status/', views.status_view, name='cratemate-status'),
    path('identify/', views.identify, name='cratemate-identify'),
    path('lookup/', views.lookup, name='cratemate-lookup'),
    path('results/', views.result_list, name='cratemate-result-list'),
    path('results/<int:pk>/', views.result_detail, name='cratemate-result-detail'),
    path('runs/', views.run_list, name='cratemate-run-list'),
]
