"""
WebSocket protocol (JSON both ways).

client -> server
    {"type": "join", "name": "Petro"}
    {"type": "click"}
    {"type": "new_game"}
    {"type": "ping"}

server -> client
    {"type": "state", "state": {...}, "event": {...} | null}   # broadcast to everyone
    {"type": "you", "player_id": "uuid" | null, "name": "..."}  # only to you
    {"type": "error", "message": "..."}                         # only to you
"""

import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from . import game

GROUP = "game"


class GameConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.session = self.scope["session"]
        self.last_click = 0.0
        await self.accept()
        if not self.session.session_key:
            await self.error("Немає сесії — онови сторінку")
            await self.close()
            return
        await self.channel_layer.group_add(GROUP, self.channel_name)
        await self.send_you()
        await self.send_json({"type": "state", "state": await self.snapshot(), "event": None})

    async def disconnect(self, code):
        await self.channel_layer.group_discard(GROUP, self.channel_name)

    # ------------------------------------------------------------ incoming

    async def receive_json(self, content, **kwargs):
        kind = content.get("type") if isinstance(content, dict) else None
        handler = getattr(self, f"on_{kind}", None) if isinstance(kind, str) else None
        if handler is None:
            await self.error(f"Не знаю, що таке {kind!r}")
            return
        try:
            await handler(content)
        except game.GameError as e:
            await self.error(str(e))

    async def on_ping(self, content):
        await database_sync_to_async(game.touch)(self.key)

    async def on_join(self, content):
        player = await self.join_and_remember(str(content.get("name", "")))
        await self.send_you()
        await self.broadcast({"kind": "join", "name": player.name})

    async def on_click(self, content):
        now = time.monotonic()
        if (now - self.last_click) * 1000 < settings.GAME["MIN_CLICK_INTERVAL_MS"]:
            return  # too fast, silently ignore — autoclickers go home
        self.last_click = now
        result = await database_sync_to_async(game.click)(self.key)
        event = {"kind": "click", "name": result.player.name, "player_id": str(result.player.public_id)}
        if result.won:
            event = {"kind": "win", "name": result.player.name}
        await self.broadcast(event)

    async def on_new_game(self, content):
        await database_sync_to_async(game.new_game)(self.key)
        await self.broadcast({"kind": "new_game"})

    # ------------------------------------------------------------ outgoing

    async def broadcast(self, event):
        await self.channel_layer.group_send(
            GROUP, {"type": "game.state", "state": await self.snapshot(), "event": event}
        )

    async def game_state(self, message):
        """Handler for group messages with type 'game.state'."""
        if message["event"] and message["event"]["kind"] == "new_game":
            await self.send_you()  # nobody is joined in a fresh game
        await self.send_json({"type": "state", "state": message["state"], "event": message["event"]})

    async def send_you(self):
        await self.send_json({"type": "you", **await self.who_am_i()})

    async def error(self, message):
        await self.send_json({"type": "error", "message": message})

    # ------------------------------------------------------------ helpers

    @property
    def key(self):
        return self.session.session_key

    # The session loads lazily from the DB, so every touch of it has to be sync.

    @database_sync_to_async
    def snapshot(self):
        return game.snapshot()

    @database_sync_to_async
    def who_am_i(self):
        player = game.find_player(self.key)
        return {
            "player_id": str(player.public_id) if player else None,
            "name": player.name if player else self.session.get("name", ""),  # prefill the input
        }

    @database_sync_to_async
    def join_and_remember(self, name):
        player = game.join(self.key, name)
        self.session["name"] = player.name
        self.session.save()
        return player
