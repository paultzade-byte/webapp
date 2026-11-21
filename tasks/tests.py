from datetime import timedelta
from unittest import mock

from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.sessions import SessionMiddlewareStack
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.db import SessionStore
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from config.routing import websocket_urlpatterns

from . import bot, game
from .models import Game, Player

# Small numbers so tests stay readable: 6 clicks each, 2 of them deposit.
# The finish line is out of reach unless a test moves it, so old "run out = draw" tests still hold.
RULES = {
    "BUDGET": 6, "DEPOSIT": 2, "FINISH": 100, "TARGET_AT": (3, 5), "SECRET_CHANCE": (1.0, 1.0),
    "IDLE_SECONDS": 5,
    "TRAP_CHANCE": 0, "LOCK_SECONDS": 2, "CARD_AT": (3,), "BATTERY": 3, "SABOTAGE": 2,
    "TICK_SECONDS": 0, "STALE_MINUTES": 5, "ONLINE_SECONDS": 30,
    "MIN_CLICK_INTERVAL_MS": 0, "HALL_OF_FAME_SIZE": 5, "BOT_CPS": 4,
    "ROB": 5, "ROB_AT": (100, 100), "ROB_MIN_PLAYERS": 3,  # privilege off unless a test sets rob_at
    "BOT_THINK_SECONDS": (0, 0),
}
SEC = timedelta(seconds=1)


def kinds(result):
    return [e["kind"] for e in result.events]


@override_settings(GAME=RULES)
class GameTestCase(TestCase):
    def setUp(self):
        self.t = timezone.now()

    def set_secret(self, value):
        Game.objects.filter(pk=game.current_game().pk).update(secret=value)

    def player(self, session):
        return Player.objects.get(session_key=session, game=game.current_game())

    def clicks(self, session, n, step=SEC):
        """n clicks, one per `step`, all within the idle limit."""
        for _ in range(n):
            self.t += step
            result = game.click(session, now=self.t)
        return result

    def give_card(self, session, card):
        Player.objects.filter(session_key=session).update(card=card)


class JoinTests(GameTestCase):
    def test_first_player_is_starter_and_gets_budget(self):
        a = game.join("a", "  Petro  ")
        game.join("b", "Mykola")
        self.assertEqual(a.name, "Petro")
        self.assertEqual(a.budget, 6)
        self.assertEqual(game.current_game().starter, a)

    def test_join_is_idempotent_and_names_unique_case_insensitive(self):
        self.assertEqual(game.join("a", "Petro").pk, game.join("a", "Whatever").pk)
        with self.assertRaises(game.GameError):
            game.join("b", "pEtRo")

    def test_bad_names(self):
        for bad in ("   ", "x" * 33):
            with self.assertRaises(game.GameError):
                game.join("s", bad)


class FinishAndSecretTests(GameTestCase):
    """The owner's core rule: race to your own FINISH-th click; the winning click is a maybe-bonus."""

    @override_settings(GAME={**RULES, "FINISH": 6})
    def test_first_to_make_all_their_clicks_wins(self):
        game.join("a", "A")
        game.join("b", "B")
        self.set_secret(None)
        for _ in range(5):  # take turns so nobody idles
            self.clicks("a", 1)
            self.clicks("b", 1)
        r = self.clicks("a", 1)
        self.assertEqual(kinds(r), ["click", "finish"])
        g = game.current_game()
        self.assertEqual(g.winner.name, "A")
        self.assertTrue(g.is_finished)
        with self.assertRaises(game.GameError):
            self.clicks("b", 1)

    @override_settings(GAME={**RULES, "FINISH": 6})
    def test_burned_clicks_do_not_bring_you_to_the_finish(self):
        game.join("a", "A")
        game.join("b", "B")
        self.set_secret(None)
        self.clicks("a", 5)
        self.clicks("b", 1)
        game.tick(now=self.t + 10 * SEC)  # a idles: the last click burns
        a = self.player("a")
        self.assertEqual((a.budget, a.burned, game.progress(a)), (0, 1, 5))
        self.assertIsNone(game.current_game().winner)

    @override_settings(GAME={**RULES, "FINISH": 6})
    def test_battery_lets_you_reach_the_finish_after_sabotage(self):
        game.join("a", "A")
        game.join("b", "B")
        self.set_secret(None)
        self.clicks("a", 1)
        self.give_card("b", "sabotage")
        game.play_card("b", str(self.player("a").public_id), now=self.t)
        self.assertEqual(self.player("a").budget, 3)  # 1 + 3 left = 4 < 6, can't finish now
        self.give_card("a", "battery")
        game.play_card("a", now=self.t)
        r = self.clicks("a", 5)
        self.assertEqual(kinds(r)[-1], "finish")

    def test_the_secret_is_found_before_anyone_finishes(self):
        game.join("a", "A")
        self.set_secret(3)  # battle click 3 = 5th of your own
        with override_settings(GAME={**RULES, "FINISH": 6}):
            r = self.clicks("a", 5)
        self.assertEqual(kinds(r), ["win"])

    def test_no_secret_at_all_is_possible_and_stays_hidden(self):
        game.join("a", "A")
        self.set_secret(None)
        snap = game.snapshot()
        self.assertIsNone(snap["secret"])
        self.assertEqual(snap["window"], [1, 3])
        with mock.patch.object(game, "draw_card", return_value="shield"):
            r = self.clicks("a", 6)
        self.assertNotIn("win", kinds(r))
        self.assertIn("draw", kinds(r))
        self.assertIsNone(game.snapshot()["secret"])  # finished and still None = there was none

    def test_roll_secret_rolls_its_own_chance_then_a_spot_in_the_window(self):
        with override_settings(GAME={**RULES, "SECRET_CHANCE": (0.0, 0.0)}):
            self.assertIsNone(game.roll_secret())
        with override_settings(GAME={**RULES, "SECRET_CHANCE": (1.0, 1.0)}):
            self.assertTrue(all(1 <= game.roll_secret() <= 3 for _ in range(50)))
        with override_settings(GAME={**RULES, "SECRET_CHANCE": (0.0, 1.0)}), \
                mock.patch.object(game.rng, "uniform", return_value=0.3) as chance, \
                mock.patch.object(game.rng, "random", return_value=0.5):
            self.assertIsNone(game.roll_secret())  # 0.5 >= 0.3: no winning click this game
            chance.assert_called_once_with(0.0, 1.0)

    def test_default_settings_keep_the_secret_before_the_finish(self):
        from config.settings import GAME as real
        self.assertEqual(real["TARGET_AT"], (11, 18))
        self.assertLess(real["TARGET_AT"][1], real["FINISH"])
        self.assertGreater(real["TARGET_AT"][0], real["DEPOSIT"])


class DepositAndBattleTests(GameTestCase):
    def test_deposit_does_not_move_counter_then_battle_does(self):
        game.join("a", "A")
        self.set_secret(3)
        r = self.clicks("a", 1)
        self.assertEqual(kinds(r), ["deposit"])
        r = self.clicks("a", 1)
        self.assertEqual(kinds(r), ["armed"])
        self.assertEqual(game.current_game().counter, 0)
        r = self.clicks("a", 1)
        self.assertEqual(kinds(r), ["click"])
        self.assertEqual(game.current_game().counter, 1)
        self.assertEqual(self.player("a").budget, 3)

    def test_hitting_the_secret_wins(self):
        game.join("a", "A")
        game.join("b", "B")
        self.set_secret(2)
        self.clicks("a", 2)
        self.clicks("b", 2)
        self.clicks("a", 1)
        r = self.clicks("b", 1)
        self.assertEqual(kinds(r), ["win"])
        g = game.current_game()
        self.assertEqual(g.winner.name, "B")
        with self.assertRaises(game.GameError):
            self.clicks("a", 1)

    def test_snapshot_hides_secret_until_the_end(self):
        game.join("secret-session", "A")
        self.set_secret(2)
        snap = game.snapshot()
        self.assertIsNone(snap["secret"])
        self.assertNotIn("secret-session", repr(snap))
        self.assertEqual(snap["window"], [1, 3])
        self.clicks("secret-session", 4)
        self.assertEqual(game.snapshot()["secret"], 2)

    def test_out_of_clicks(self):
        game.join("a", "A")
        self.set_secret(100)
        self.clicks("a", 6)
        with self.assertRaises(game.GameError):
            self.clicks("a", 1)

    def test_everyone_out_of_clicks_is_a_draw(self):
        game.join("a", "A")
        self.set_secret(100)
        with mock.patch.object(game, "draw_card", return_value="shield"):  # not a battery
            r = self.clicks("a", 6)
        self.assertIn("draw", kinds(r))
        self.assertTrue(game.current_game().is_finished)
        self.assertEqual(game.snapshot()["secret"], 100)

    def test_throttle_is_per_player(self):
        game.join("a", "A")
        with override_settings(GAME={**RULES, "MIN_CLICK_INTERVAL_MS": 500}):
            self.clicks("a", 3, step=timedelta(milliseconds=100))
        self.assertEqual(self.player("a").deposited, 1)


class TrapTests(GameTestCase):
    def test_slip_does_not_count(self):
        game.join("a", "A")
        with mock.patch.object(game, "roll_trap", return_value="slip"):
            r = self.clicks("a", 1)
        self.assertEqual(r.notices[0]["trap"], "slip")
        self.assertEqual(r.events, [])
        p = self.player("a")
        self.assertEqual((p.deposited, p.budget), (0, 6))

    def test_lock_counts_but_blocks_the_button(self):
        game.join("a", "A")
        with mock.patch.object(game, "roll_trap", return_value="lock"):
            self.clicks("a", 1)
        r = self.clicks("a", 1)  # 1s later, lock is 2s
        self.assertEqual(r.notices[0]["kind"], "locked")
        self.assertEqual(self.player("a").deposited, 1)
        self.clicks("a", 1, step=2 * SEC)
        self.assertEqual(self.player("a").deposited, 2)

    def test_no_traps_in_battle(self):
        game.join("a", "A")
        self.set_secret(100)
        self.clicks("a", 2)
        with mock.patch.object(game, "roll_trap", return_value="slip"):
            r = self.clicks("a", 1)
        self.assertEqual(kinds(r), ["click"])


class IdleTests(GameTestCase):
    def test_no_penalty_before_first_click(self):
        game.join("a", "A")
        game.tick(now=self.t + 60 * SEC)
        self.assertEqual(self.player("a").budget, 6)

    def test_idle_in_deposit_costs_a_click(self):
        game.join("a", "A")
        self.clicks("a", 1)
        r = game.tick(now=self.t + 11 * SEC)  # 2 penalties
        self.assertEqual(kinds(r), ["idle", "idle"])
        p = self.player("a")
        self.assertEqual((p.budget, p.deposited), (3, 1))

    def test_idle_in_battle_burns_and_moves_counter(self):
        game.join("a", "A")
        self.set_secret(3)
        self.clicks("a", 2)
        r = game.tick(now=self.t + 6 * SEC)
        self.assertEqual(kinds(r), ["burn"])
        self.assertEqual(game.current_game().counter, 1)
        self.assertEqual(self.player("a").burned, 1)

    def test_burning_the_winning_click_rerolls_the_secret(self):
        game.join("a", "A")
        game.join("b", "B")
        self.set_secret(1)
        self.clicks("a", 2)
        self.clicks("b", 2)  # b keeps clicking, a idles
        with mock.patch.object(game.rng, "randint", return_value=3) as roll:
            r = game.tick(now=self.t + 4 * SEC)  # a's last click was 2s before b's last
        roll.assert_called_once_with(2, 3)  # still inside the window (battle 1..3), after the burn
        self.assertEqual(kinds(r), ["burn_win"])
        g = game.current_game()
        self.assertEqual((g.counter, g.secret), (1, 3))
        self.assertFalse(g.is_finished)

    def test_burning_the_last_possible_winning_click_removes_it(self):
        game.join("a", "A")
        game.join("b", "B")
        self.set_secret(3)  # the very end of the window (battle 1..3)
        self.clicks("a", 2)
        self.clicks("b", 4)  # counter 2, a idles
        r = game.tick(now=self.t + 4 * SEC)
        self.assertEqual(kinds(r), ["burn_win"])
        self.assertIsNone(game.current_game().secret)

    def test_penalty_is_charged_lazily_on_the_next_click_too(self):
        game.join("a", "A")
        self.clicks("a", 1)
        self.clicks("a", 1, step=6 * SEC)  # 1 penalty, then the click itself
        p = self.player("a")
        self.assertEqual((p.budget, p.deposited), (3, 2))

    def test_shield_absorbs_one_idle_penalty(self):
        game.join("a", "A")
        self.clicks("a", 1)
        Player.objects.filter(session_key="a").update(shielded=True)
        r = game.tick(now=self.t + 11 * SEC)
        self.assertEqual(kinds(r), ["shield", "idle"])
        self.assertEqual(self.player("a").budget, 4)


class CardTests(GameTestCase):
    def setUp(self):
        super().setUp()
        game.join("a", "A")
        game.join("b", "B")
        self.set_secret(100)

    def test_card_is_awarded_at_milestone_and_hand_holds_one(self):
        with mock.patch.object(game, "draw_card", return_value="battery"):
            r = self.clicks("a", 3)
        self.assertEqual(r.notices, [{"kind": "card", "card": "battery"}])
        self.assertEqual(self.player("a").card, "battery")
        self.assertTrue(game.snapshot()["players"][0]["has_card"])
        self.assertNotIn("battery", repr(game.snapshot()["players"]))  # others can't see which

    def test_battery(self):
        self.give_card("a", "battery")
        r = game.play_card("a")
        self.assertEqual(kinds(r), ["card"])
        self.assertEqual(self.player("a").budget, 9)
        self.assertEqual(self.player("a").card, "")

    def test_sabotage_needs_a_valid_target(self):
        self.give_card("a", "sabotage")
        for target in (None, "not-a-uuid", str(self.player("a").public_id)):
            with self.assertRaises(game.GameError):
                game.play_card("a", target)
        r = game.play_card("a", str(self.player("b").public_id))
        self.assertEqual(kinds(r), ["card"])
        self.assertEqual(self.player("b").budget, 4)

    def test_swap(self):
        self.clicks("a", 4)
        self.clicks("b", 1)
        self.give_card("a", "swap")
        game.play_card("a", str(self.player("b").public_id))
        self.assertEqual((self.player("a").budget, self.player("b").budget), (5, 2))

    def test_cannot_swap_with_someone_who_never_clicked(self):
        self.clicks("a", 4)
        self.give_card("a", "swap")
        with self.assertRaises(game.GameError):
            game.play_card("a", str(self.player("b").public_id))
        self.assertEqual(self.player("a").card, "swap")  # the card is not wasted
        self.assertFalse(game.snapshot()["players"][1]["clicked"])

    def test_shield_is_secret_and_blocks_sabotage(self):
        self.give_card("b", "shield")
        r = game.play_card("b")
        self.assertEqual(r.events, [])  # nobody is told
        self.assertEqual(r.notices, [{"kind": "shield_up"}])
        self.give_card("a", "sabotage")
        r = game.play_card("a", str(self.player("b").public_id))
        self.assertEqual(kinds(r), ["shield"])
        b = self.player("b")
        self.assertEqual((b.budget, b.shielded), (6, False))

    def test_revived_player_does_not_pay_for_time_at_zero(self):
        self.clicks("a", 6)  # a is at 0 clicks
        self.give_card("a", "battery")
        self.t += 60 * SEC
        game.play_card("a", now=self.t)
        game.tick(now=self.t + 2 * SEC)
        self.assertEqual(self.player("a").budget, 3)


class RobTests(GameTestCase):
    def setUp(self):
        super().setUp()
        for key in "abc":
            game.join(key, key.upper())
        self.set_secret(100)

    def give_privilege(self, session):
        Player.objects.filter(session_key=session).update(can_rob=True)

    def rob(self, robber, victim):
        return game.rob(robber, str(self.player(victim).public_id), now=self.t)

    def test_privilege_drops_on_the_secret_click_and_stays_secret(self):
        Player.objects.filter(session_key="a").update(rob_at=2)
        self.assertEqual(self.clicks("a", 1).notices, [])
        self.assertEqual(self.clicks("a", 1).notices, [{"kind": "privilege"}])
        self.assertTrue(self.player("a").can_rob)
        self.assertNotIn("rob", repr(game.snapshot()["players"]))
        self.assertTrue(game.private_state("a")["can_rob"])

    def test_no_privilege_with_two_players(self):
        Player.objects.filter(session_key="c").delete()
        Player.objects.filter(session_key="a").update(rob_at=1)
        r = self.clicks("a", 1)
        self.assertEqual(r.notices, [{"kind": "privilege_missed", "need": 3}])
        self.assertFalse(self.player("a").can_rob)

    def test_rob_a_sleeper_for_five(self):
        self.give_privilege("a")
        r = self.rob("a", "c")  # c never clicked: still fair game for robbers
        self.assertEqual(r.events[0]["amount"], 5)
        self.assertEqual((self.player("a").budget, self.player("c").budget), (11, 1))
        self.assertFalse(self.player("a").can_rob)
        with self.assertRaises(game.GameError):
            self.rob("a", "b")  # once per privilege

    def test_never_rob_to_the_bone(self):
        self.give_privilege("a")
        Player.objects.filter(session_key="c").update(budget=3)
        self.assertEqual(self.rob("a", "c").events[0]["amount"], 2)
        self.give_privilege("a")
        with self.assertRaises(game.GameError):
            self.rob("a", "c")  # 1 click left — off limits
        self.assertEqual(self.player("c").budget, 1)

    def test_rob_needs_privilege_a_target_and_not_yourself(self):
        with self.assertRaises(game.GameError):
            self.rob("a", "b")
        self.give_privilege("a")
        with self.assertRaises(game.GameError):
            game.rob("a", None)
        with self.assertRaises(game.GameError):
            self.rob("a", "a")
        self.assertTrue(self.player("a").can_rob)

    def test_shield_stops_a_robbery(self):
        self.give_privilege("a")
        Player.objects.filter(session_key="c").update(shielded=True)
        r = self.rob("a", "c")
        self.assertEqual((r.events[0]["kind"], r.events[0]["against"]), ("shield", "rob"))
        self.assertEqual((self.player("a").budget, self.player("c").budget), (6, 6))
        self.assertFalse(self.player("a").can_rob)

    def test_privilege_sleeps_when_players_drop_below_three(self):
        self.give_privilege("a")
        Player.objects.filter(session_key="c").delete()
        with self.assertRaises(game.GameError):
            self.rob("a", "b")
        self.assertTrue(self.player("a").can_rob)


class RestartTests(GameTestCase):
    def test_only_starter_can_restart_running_game(self):
        game.join("a", "A")
        game.join("b", "B")
        self.clicks("b", 1)
        with self.assertRaises(game.GameError):
            game.new_game("b")
        old = game.current_game()
        self.assertNotEqual(old.pk, game.new_game("a").pk)
        self.assertIsNone(game.find_player("a"))

    def test_anyone_can_restart_if_starter_offline(self):
        game.join("a", "A")
        game.join("b", "B")
        self.clicks("b", 1)
        Player.objects.filter(name="A").update(last_seen=timezone.now() - timedelta(minutes=1))
        game.new_game("b")


class BotToggleTests(GameTestCase):
    def setUp(self):
        super().setUp()
        game.join("a", "A")
        game.join("b", "B")

    def bot(self):
        return Player.objects.filter(game=game.current_game(), is_bot=True).first()

    def test_starter_toggles_bot_on_and_off(self):
        r = game.set_bot("a", True)
        self.assertEqual(kinds(r), ["bot_on"])
        snap = game.snapshot()
        self.assertTrue(snap["with_bot"])
        bots = [p for p in snap["players"] if p["bot"]]
        self.assertEqual([p["name"] for p in bots], ["🤖 Бот"])
        self.assertTrue(bots[0]["online"])
        self.assertEqual(kinds(game.set_bot("a", False)), ["bot_off"])
        self.assertIsNone(self.bot())

    def test_toggling_twice_is_harmless(self):
        game.set_bot("a", True)
        self.assertEqual(game.set_bot("a", True).events, [])
        self.assertEqual(Player.objects.filter(is_bot=True).count(), 1)

    def test_only_starter_or_joined_players(self):
        with self.assertRaises(game.GameError):
            game.set_bot("b", True)  # not the starter, starter is online
        with self.assertRaises(game.GameError):
            game.set_bot("stranger", True)

    def test_bot_never_becomes_starter_and_new_games_keep_it(self):
        game.set_bot("a", True)
        self.clicks("a", 1)
        fresh = game.new_game("a")
        self.assertTrue(fresh.with_bot)
        self.assertIsNotNone(self.bot())
        self.assertIsNone(fresh.starter)
        game.join("b", "B")
        self.assertEqual(game.current_game().starter.name, "B")

    def test_bot_takes_another_name_if_its_name_is_taken(self):
        game.join("c", "🤖 бот")
        game.set_bot("a", True)
        self.assertEqual(self.bot().name, "🤖 Бот 2")


@override_settings(GAME={**RULES, "CARD_AT": ()})  # cards are handed out by the tests here
class BotStrategyTests(GameTestCase):
    def setUp(self):
        super().setUp()
        game.join("a", "A")
        game.set_bot("a", True)
        self.set_secret(100)

    def bot_player(self):
        return Player.objects.get(game=game.current_game(), is_bot=True)

    def bot_steps(self, n, step=timedelta(milliseconds=300)):
        results = []
        for _ in range(n):
            self.t += step
            results.append(bot.step(now=self.t))
        return results

    def test_bot_waits_for_humans_to_start(self):
        self.assertEqual(self.bot_steps(3), [None, None, None])
        self.clicks("a", 1)
        self.assertEqual(kinds(self.bot_steps(1)[0]), ["deposit"])

    def test_bot_deposits_then_fights_and_never_idles(self):
        self.clicks("a", 1)
        results = self.bot_steps(4)
        self.assertEqual([kinds(r) for r in results], [["deposit"], ["armed"], ["click"], ["click"]])
        self.assertEqual(self.bot_player().burned, 0)

    def test_bot_trap_is_public_and_slows_it_down(self):
        self.clicks("a", 1)
        with mock.patch.object(game, "roll_trap", return_value="modal"):
            r = self.bot_steps(1)[0]
        self.assertIn({"kind": "bot_trap", "trap": "modal", "name": "🤖 Бот"}, r.events)
        self.assertIsNotNone(self.bot_player().disabled_until)
        self.assertEqual(self.bot_steps(1, step=timedelta(milliseconds=100))[0].events, [])  # still confused

    def test_bot_plays_battery_and_shield_at_once(self):
        self.clicks("a", 1)
        self.give_card(game.BOT_SESSION, "battery")
        r = self.bot_steps(1)[0]
        self.assertEqual(r.events[0]["card"], "battery")
        self.give_card(game.BOT_SESSION, "shield")
        self.bot_steps(1)
        self.assertTrue(self.bot_player().shielded)

    def test_bot_thinks_before_playing_a_card(self):
        self.clicks("a", 1)
        self.give_card(game.BOT_SESSION, "battery")
        with override_settings(GAME={**RULES, "CARD_AT": (), "BOT_THINK_SECONDS": (2, 2)}):
            first = self.bot_steps(1)[0]
            later = self.bot_steps(1, step=timedelta(milliseconds=2100))[0]
        self.assertEqual(kinds(first), ["deposit"])  # just got it, still thinking
        self.assertEqual(later.events[0]["card"], "battery")

    def test_bot_robs_the_richest_even_a_sleeper(self):
        game.join("c", "C")  # never clicks, keeps all 6
        self.clicks("a", 1)
        Player.objects.filter(session_key=game.BOT_SESSION).update(can_rob=True)
        r = self.bot_steps(1)[0]
        self.assertEqual((r.events[0]["kind"], r.events[0]["target"], r.events[0]["amount"]), ("rob", "C", 5))

    def test_bot_sabotages_the_biggest_threat(self):
        game.join("b", "B")
        self.clicks("a", 3)  # A: in battle, 3 clicks left
        self.clicks("b", 1)  # B: deposit 1/2, 5 clicks -> 4 battle clicks left
        self.give_card(game.BOT_SESSION, "sabotage")
        r = self.bot_steps(1)[0]
        self.assertEqual(r.events[0]["target"], "B")

    def test_bot_swaps_only_when_it_pays_off(self):
        game.join("c", "C")  # a sleeper with 6 clicks: richer, but swap can't touch him
        self.clicks("a", 1)  # A has 5 clicks, the bot 6
        self.give_card(game.BOT_SESSION, "swap")
        for _ in range(5):
            before = self.bot_player().budget
            r = self.bot_steps(1)[0]
            if "card" in kinds(r):
                self.assertEqual(before, 3)  # first moment A (5) has more than bot + 1
                self.assertEqual(r.events[0]["target"], "A")
                self.assertEqual(self.bot_player().budget, 5)
                return
        self.fail("the bot never swapped")


def make_session():
    s = SessionStore()
    s.create()
    return s


@override_settings(GAME={**RULES, "TARGET_AT": (3, 3), "CARD_AT": ()})  # secret is always 1
class WebsocketTests(TransactionTestCase):
    app = SessionMiddlewareStack(URLRouter(websocket_urlpatterns))

    async def open(self):
        session = await sync_to_async(make_session)()
        comm = WebsocketCommunicator(
            self.app, "/ws/game/", headers=[(b"cookie", f"sessionid={session.session_key}".encode())]
        )
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        self.assertEqual((await comm.receive_json_from())["type"], "you")
        self.assertEqual((await comm.receive_json_from())["type"], "state")
        return comm

    async def test_full_round_over_websocket(self):
        a, b = await self.open(), await self.open()

        await a.send_json_to({"type": "join", "name": "Alice"})
        self.assertIsNotNone((await a.receive_json_from())["player_id"])
        for comm in (a, b):
            self.assertEqual((await comm.receive_json_from())["events"], [{"kind": "join", "name": "Alice"}])

        await b.send_json_to({"type": "click"})
        self.assertEqual((await b.receive_json_from())["type"], "error")  # not joined

        expected = ["deposit", "armed", "win"]
        for kind in expected:
            await a.send_json_to({"type": "click"})
        for comm in (a, b):
            got = [(await comm.receive_json_from())["events"][0]["kind"] for _ in expected]
            self.assertEqual(got, expected)

        await b.send_json_to({"type": "new_game"})
        for comm in (a, b):
            msg = await comm.receive_json_from()
            self.assertEqual(msg["events"], [{"kind": "new_game"}])
            self.assertIsNone((await comm.receive_json_from())["player_id"])

        await a.disconnect()
        await b.disconnect()

    async def test_trap_notice_goes_only_to_the_clicker(self):
        a = await self.open()
        await a.send_json_to({"type": "join", "name": "Alice"})
        await a.receive_json_from()
        await a.receive_json_from()
        with mock.patch.object(game, "roll_trap", return_value="modal"):
            await a.send_json_to({"type": "click"})
            msgs = [await a.receive_json_from() for _ in range(3)]
        self.assertEqual([m["type"] for m in msgs], ["notice", "you", "state"])
        self.assertEqual(msgs[0]["trap"], "modal")
        await a.disconnect()

    async def test_garbage_message_gets_error(self):
        comm = await self.open()
        await comm.send_json_to({"type": "__init__"})
        self.assertEqual((await comm.receive_json_from())["type"], "error")
        await comm.disconnect()


@override_settings(INVITES_REQUIRED=True, INVITE_MAX_DEVICES=2)
class InviteTests(TestCase):
    def setUp(self):
        from . import invites
        self.inv = invites
        self.code = invites.create(1, hours=1)[0].code

    def test_strangers_get_404_everywhere(self):
        for url in ("/", "/api/state/", "/admin/", "/AAAAAAAAAAAA/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_the_link_lets_you_in_and_remembers_you(self):
        r = self.client.get(f"/{self.code}")
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/state/").status_code, 200)

    def test_one_link_works_on_at_most_two_devices(self):
        from django.test import Client
        for _ in range(2):
            self.assertEqual(Client().get(f"/{self.code}").status_code, 302)
        third = Client()
        self.assertEqual(third.get(f"/{self.code}").status_code, 404)
        self.assertEqual(third.get("/").status_code, 404)

    def test_expired_or_revoked_link_stops_working_even_after_entering(self):
        self.client.get(f"/{self.code}")
        Invite = self.inv.Invite
        Invite.objects.update(expires_at=timezone.now())
        self.assertEqual(self.client.get("/").status_code, 404)
        self.assertEqual(self.client.get(f"/{self.code}").status_code, 404)

    def test_codes_are_unique_and_url_safe(self):
        codes = {i.code for i in self.inv.create(50, hours=1)}
        self.assertEqual(len(codes), 50)
        self.assertTrue(all(self.inv.CODE_RE.match(c) for c in codes))

    @override_settings(INVITES_REQUIRED=False)
    def test_off_by_default_for_local_play(self):
        self.assertEqual(self.client.get("/").status_code, 200)


@override_settings(GAME={**RULES, "CARD_AT": ()}, INVITES_REQUIRED=True)
class InviteWebsocketTests(TransactionTestCase):
    app = SessionMiddlewareStack(URLRouter(websocket_urlpatterns))

    async def test_websocket_without_invite_is_closed(self):
        session = await sync_to_async(make_session)()
        comm = WebsocketCommunicator(
            self.app, "/ws/game/", headers=[(b"cookie", f"sessionid={session.session_key}".encode())]
        )
        connected, _ = await comm.connect()
        self.assertTrue(connected)  # accepted, then closed straight away
        self.assertEqual((await comm.receive_output())["type"], "websocket.close")
        await comm.disconnect()
