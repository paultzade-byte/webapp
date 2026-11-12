from django.urls import path

from tasks.consumers import GameConsumer

websocket_urlpatterns = [
    path("ws/game/", GameConsumer.as_asgi()),  # pyright: ignore
]
