from django.urls import path
from . import views

urlpatterns = [
    path('add/', views.add_item, name='dig-add'),
    path('batch/', views.batch_add, name='dig-batch'),
    path('check/', views.check_items, name='dig-check'),
    path('status/', views.dig_status, name='dig-status'),
    path('videos/<int:release_id>/', views.release_videos, name='dig-videos'),
    path('player/', views.player_page, name='dig-player'),
    path('embed/', views.embed_proxy, name='dig-embed'),
    path('yt-search/', views.yt_search, name='dig-yt-search'),
    path('bandcamp-streams/', views.bandcamp_streams, name='dig-bandcamp-streams'),
    path('bandcamp-player/', views.bandcamp_player, name='dig-bandcamp-player'),
]
