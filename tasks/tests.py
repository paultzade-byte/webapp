from datetime import timedelta

from channels.routing import URLRouter
from channels.sessions import SessionMiddlewareStack
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.db import SessionStore
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from config.routing import websocket_urlpatterns

from . import game
from .models import Game, Player

SMALL = {
    "TARGET": 3, "STALE_MINUTES": 5, "ONLINE_SECONDS": 30,
    "MIN_CLICK_INTERVAL_MS": 0, "HALL_OF_FAME_SIZE": 5,
}


@override_settings(GAME=SMALL)
class GameRulesTests(TestCase):
    def test_join_and_first_player_is_starter(self):
        a = game.join("sess-a", "  Petro  ")
        game.join("sess-b", "Mykola")
        g = game.current_game()
        self.assertEqual(a.name, "Petro")
        self.assertEqual(g.starter, a)

    def test_join_is_idempotent_and_names_unique_case_insensitive(self):
        a1 = game.join("sess-a", "Petro")
        a2 = game.join("sess-a", "Whatever")
        self.assertEqual(a1.pk, a2.pk)
        with self.assertRaises(game.GameError):
            game.join("sess-b", "pEtRo")

    def test_blank_or_long_names_rejected(self):
        with self.assertRaises(game.GameError):
            game.join("s", "   ")
        with self.assertRaises(game.GameError):
            game.join("s", "x" * 33)

    def test_click_requires_join(self):
        with self.assertRaises(game.GameError):
            game.click("nobody")

    def test_last_click_wins_and_game_freezes(self):
        game.join("a", "A")
        game.join("b", "B")
        game.click("a")
        game.click("a")
        result = game.click("b")
        self.assertTrue(result.won)
        g = game.current_game()
        self.assertEqual(g.winner.name, "B")
        self.assertTrue(g.is_finished)
        with self.assertRaises(game.GameError):
            game.click("a")
        snap = game.snapshot()
        self.assertEqual([p["name"] for p in snap["players"]], ["A", "B"])  # sorted by clicks
        self.assertEqual(snap["hall_of_fame"][0]["name"], "B")

    def test_only_starter_can_restart_running_game(self):
        game.join("a", "A")
        game.join("b", "B")
        game.click("b")
        with self.assertRaises(game.GameError):
            game.new_game("b")
        old = game.current_game()
        new = game.new_game("a")
        self.assertNotEqual(old.pk, new.pk)
        self.assertIsNone(game.find_player("a"))  # everyone must re-join

    def test_anyone_can_restart_if_starter_offline_or_game_stale(self):
        game.join("a", "A")
        game.join("b", "B")
        game.click("b")
        Player.objects.filter(name="A").update(last_seen=timezone.now() - timedelta(minutes=1))
        game.new_game("b")  # starter offline -> ok

        game.join("a", "A")
        game.join("b", "B")
        game.click("b")
        Game.objects.filter(pk=game.current_game().pk).update(
            last_click_at=timezone.now() - timedelta(minutes=10)
        )
        game.new_game("b")  # stale -> ok

    def test_anyone_can_restart_after_win(self):
        game.join("a", "A")
        game.join("b", "B")
        for _ in range(3):
            game.click("b")
        game.new_game("b")
        self.assertEqual(game.current_game().counter, 0)

    def test_snapshot_does_not_leak_session_keys(self):
        game.join("super-secret-session", "A")
        self.assertNotIn("super-secret-session", repr(game.snapshot()))


def make_session():
    s = SessionStore()
    s.create()
    return s


@override_settings(GAME=SMALL)
class WebsocketTests(TransactionTestCase):
    app = SessionMiddlewareStack(URLRouter(websocket_urlpatterns))

    async def open(self, session):
        comm = WebsocketCommunicator(
            self.app, "/ws/game/", headers=[(b"cookie", f"sessionid={session.session_key}".encode())]
        )
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        you = await comm.receive_json_from()
        state = await comm.receive_json_from()
        self.assertEqual(you["type"], "you")
        self.assertEqual(state["type"], "state")
        return comm

    async def test_full_round_over_websocket(self):
        from asgiref.sync import sync_to_async

        sa, sb = await sync_to_async(make_session)(), await sync_to_async(make_session)()
        a, b = await self.open(sa), await self.open(sb)

        await a.send_json_to({"type": "join", "name": "Alice"})
        self.assertIsNotNone((await a.receive_json_from())["player_id"])  # you
        for comm in (a, b):
            msg = await comm.receive_json_from()
            self.assertEqual(msg["event"], {"kind": "join", "name": "Alice"})

        await b.send_json_to({"type": "click"})
        self.assertEqual((await b.receive_json_from())["type"], "error")  # not joined

        for _ in range(3):
            await a.send_json_to({"type": "click"})
        for comm in (a, b):
            for _ in range(3):
                msg = await comm.receive_json_from()
            self.assertEqual(msg["event"]["kind"], "win")
            self.assertEqual(msg["state"]["winner"], "Alice")

        await b.send_json_to({"type": "new_game"})
        for comm in (a, b):
            self.assertEqual((await comm.receive_json_from())["player_id"], None)  # you reset
            msg = await comm.receive_json_from()
            self.assertEqual(msg["event"]["kind"], "new_game")
            self.assertEqual(msg["state"]["counter"], 0)

        await a.disconnect()
        await b.disconnect()

    async def test_garbage_message_gets_error(self):
        from asgiref.sync import sync_to_async

        comm = await self.open(await sync_to_async(make_session)())
        await comm.send_json_to({"type": "__init__"})
        self.assertEqual((await comm.receive_json_from())["type"], "error")
        await comm.disconnect()
