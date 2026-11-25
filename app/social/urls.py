from django.urls import path

from . import views

app_name = "social"

urlpatterns = [
    path("", views.friends_page, name="friends"),
    path("inviter/", views.invite, name="invite"),
    path("invitation/<int:pk>/accepter/", views.accept, name="accept"),
    path("invitation/<int:pk>/refuser/", views.decline, name="decline"),
    path("invitation/<int:pk>/annuler/", views.cancel, name="cancel"),
    path("retirer/<int:user_id>/", views.remove, name="remove"),
]
