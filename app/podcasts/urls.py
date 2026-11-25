from django.urls import path

from . import review_views, views

app_name = "podcasts"

urlpatterns = [
    path("", views.podcast_list, name="podcast_list"),
    path("relecture/<int:pk>/", review_views.review_series, name="review_series"),
    path(
        "relecture/<int:pk>/action/", review_views.review_series_action, name="review_series_action"
    ),
    path("relecture/episode/<int:pk>/", review_views.review_episode, name="review_episode"),
    path("<slug:slug>/", views.series_detail, name="series_detail"),
    path("<slug:slug>/episode/<int:position>.mp3", views.episode_audio, name="episode_audio"),
    path("<slug:slug>/ecoute/", views.listen_progress, name="listen_progress"),
]
