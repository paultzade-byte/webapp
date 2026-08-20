from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("save-name/", views.save_name, name="save_name"),
    path("click/", views.click, name="click"),
   path("new-game/", views.new_game, name="new_game"),
]
