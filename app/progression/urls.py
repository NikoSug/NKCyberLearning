from django.urls import path

from . import views

app_name = "progression"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("objectif/", views.set_goal, name="set_goal"),
    path("boutique/", views.shop, name="shop"),
    path("boutique/acheter/", views.buy_avatar, name="buy_avatar"),
    path("boutique/choisir/", views.equip_avatar, name="equip_avatar"),
    path("boutique/objet/acheter/", views.buy_item, name="buy_item"),
    path("boutique/objet/choisir/", views.equip_item, name="equip_item"),
]
