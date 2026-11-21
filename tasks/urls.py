from django.urls import path, re_path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("api/state/", views.state, name="state"),
    re_path(r"^(?P<code>[A-Za-z0-9_-]{12})/?$", views.invite, name="invite"),  # personal invite link
]
