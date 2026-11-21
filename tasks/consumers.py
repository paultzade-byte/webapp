"""
WebSocket protocol (JSON both ways).

client -> server
    {"type": "join", "name": "Petro"}
    {"type": "click"}
    {"type": "play_card", "target": "<player id>"}   # target only for sabotage / swap
    {"type": "new_game"}
    {"type": "set_bot", "enabled": true}
    {"type": "ping"}

server -> client
    {"type": "state", "state": {...}, "events": [...]}   # broadcast to everyone
    {"type": "you", "player_id": ..., "card": ..., ...}  # your private state, only to you
    {"type": "notice", "kind": "trap" | "card" | ...}    # something happened to you, only to you
    {"type": "error", "message": "..."}                  # only to you
"""

import asyncio
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from django.conf import settings

from . import bot, game

GROUP = "game"
log = logging.getLogger(__name__)


async def publish(result: game.Result) -> None:
    """Send fresh state + what just happened to every connected client."""
    if not (result.events or result.affected):
        return
    layer = get_channel_layer()
    assert layer is not None
    await layer.group_send(
        GROUP,
        {
            "type": "game.state",
            "state": await database_sync_to_async(game.snapshot)(),
            "events": result.events,
            "affected": sorted(result.affected),
        },
    )


# ------------------------------------------------------------ background loops
# One idle ticker and one bot per server process, alive only while somebody is
# connected. game.tick() is idempotent, so several processes ticking is fine.
# Several processes would mean several bot "brains" sharing one bot player —
# it would just click faster. Good enough for runserver / single daphne.

_connections = 0
_loops: list[asyncio.Task] = []


async def _tick_forever():
    while _connections > 0:
        await asyncio.sleep(settings.GAME["TICK_SECONDS"])
        try:
            await publish(await database_sync_to_async(game.tick)())
        except Exception:
            log.exception("idle tick failed")


async def _bot_forever():
    while _connections > 0:
        result = None
        try:
            result = await database_sync_to_async(bot.step)()
            if result is not None:
                await publish(result)
        except Exception:
            log.exception("bot step failed")
        # nothing to do -> check again in a second; playing -> human-ish jittery rhythm
        delay = 1.0 if result is None else game.rng.uniform(0.6, 1.4) / settings.GAME["BOT_CPS"]
        await asyncio.sleep(delay)


def _ensure_loops():
    """TICK_SECONDS = 0 switches both loops off (the tests drive time by hand)."""
    global _loops
    if not settings.GAME["TICK_SECONDS"] or (_loops and not all(t.done() for t in _loops)):
        return
    loop = asyncio.get_running_loop()
    _loops = [loop.create_task(_tick_forever()), loop.create_task(_bot_forever())]


class GameConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        global _connections
        self.session = self.scope["session"]
        self.player_id = None
        self.counted = False
        await self.accept()
        if not self.session.session_key:
            await self.error("Немає сесії — онови сторінку")
            await self.close()
            return
        _connections += 1
        self.counted = True
        await self.channel_layer.group_add(GROUP, self.channel_name)
        _ensure_loops()
        await self.send_you()
        await self.send_json({"type": "state", "state": await self.snapshot(), "events": []})

    async def disconnect(self, code):
        global _connections
        if self.counted:
            _connections -= 1
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
        await publish(game.Result(events=[{"kind": "join", "name": player.name}]))

    async def on_click(self, content):
        await self.deliver(await database_sync_to_async(game.click)(self.key))

    async def on_play_card(self, content):
        target = content.get("target")
        await self.deliver(await database_sync_to_async(game.play_card)(self.key, target))

    async def on_set_bot(self, content):
        enabled = content.get("enabled") is True
        await publish(await database_sync_to_async(game.set_bot)(self.key, enabled))

    async def on_new_game(self, content):
        await database_sync_to_async(game.new_game)(self.key)
        await publish(game.Result(events=[{"kind": "new_game"}]))

    # ------------------------------------------------------------ outgoing

    async def deliver(self, result: game.Result):
        for notice in result.notices:
            await self.send_json({"type": "notice", **notice})
        if result.notices:
            await self.send_you()  # a card or a lock just changed
        await publish(result)

    async def game_state(self, message):
        """Handler for group messages with type 'game.state'."""
        await self.send_json({"type": "state", "state": message["state"], "events": message["events"]})
        new_game = any(e["kind"] == "new_game" for e in message["events"])
        if new_game or (self.player_id and self.player_id in message["affected"]):
            await self.send_you()

    async def send_you(self):
        data = await self.who_am_i()
        self.player_id = data["player_id"]
        await self.send_json({"type": "you", **data})

    async def error(self, message):
        await self.send_json({"type": "error", "message": message})

    # ------------------------------------------------------------ helpers
    # The session loads lazily from the DB, so every touch of it has to be sync.

    @property
    def key(self):
        return self.session.session_key

    @database_sync_to_async
    def snapshot(self):
        return game.snapshot()

    @database_sync_to_async
    def who_am_i(self):
        return game.private_state(self.key, self.session.get("name", ""))

    @database_sync_to_async
    def join_and_remember(self, name):
        player = game.join(self.key, name)
        self.session["name"] = player.name
        self.session.save()
        return player
