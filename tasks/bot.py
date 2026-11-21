"""
The bot's brain.

It plays by exactly the same rules as people: it goes through game.click()
and game.play_card() like everyone else, gets the same traps, idle penalties
and cards. It just never gets tired.

Strategy "Валера" (greedy and simple):
  * waits until at least one human has made a first click — no head start;
  * clicks steadily at BOT_CPS and never idles, so it never burns a click;
  * traps slow it down like a person: it needs a moment to find the slipped
    button and to close the crash screen (the lock is enforced by the server);
  * cards: battery and shield go out at once, sabotage hits whoever has the
    most battle clicks left, swap only if somebody has more clicks than it.

Want a smarter or nastier bot? Change the functions below. BotTests in
tasks/tests.py show what the current one is expected to do.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from . import game
from .models import Game, Player

# How long a trap confuses the bot, seconds (min, max).
CONFUSION = {"slip": (0.3, 0.8), "modal": (0.5, 1.5)}


def step(now: datetime | None = None) -> game.Result | None:
    """One move of the bot. Returns None when there is nothing to do at all
    (no bot, game over, people haven't started), so the loop can nap longer."""
    now = now or timezone.now()
    g = game.current_game()
    bot = Player.objects.filter(game=g, is_bot=True).first()
    if bot is None or g.is_finished or not humans_started(g):
        return None
    try:
        if bot.card:
            play, target = decide_card(g, bot)
            if play:
                return game.play_card(game.BOT_SESSION, target, now=now)
        if bot.budget == 0 or (bot.disabled_until and now < bot.disabled_until):
            return game.Result()
        result = game.click(game.BOT_SESSION, now=now)
    except game.GameError:
        return game.Result()
    react_to_traps(bot, result, now)
    return result


def humans_started(g: Game) -> bool:
    return Player.objects.filter(game=g, is_bot=False, last_action_at__isnull=False).exists()


def battle_clicks_left(p: Player) -> int:
    """How many lottery tickets a player can still buy."""
    if game.in_battle(p):
        return p.budget
    return max(0, p.budget - (game.cfg("DEPOSIT") - p.deposited))


def decide_card(g: Game, bot: Player) -> tuple[bool, str | None]:
    """(play it now?, target id or None)"""
    if bot.card in (Player.Card.BATTERY, Player.Card.SHIELD):
        return True, None
    rivals = list(g.players.filter(is_bot=False))
    if not rivals:
        return False, None
    if bot.card == Player.Card.SABOTAGE:
        threat = max(rivals, key=battle_clicks_left)
        return battle_clicks_left(threat) > 0, str(threat.public_id)
    richest = max(rivals, key=lambda p: p.budget)  # swap
    return richest.budget > bot.budget + 1, str(richest.public_id)


def react_to_traps(bot: Player, result: game.Result, now: datetime) -> None:
    """The bot has no screen, so its traps become public events — everyone
    gets to laugh — and a short pause instead of a button hunt."""
    for notice in result.notices:
        if notice.get("kind") != "trap":
            continue
        trap = notice["trap"]
        result.events.append({"kind": "bot_trap", "trap": trap, "name": bot.name})
        if trap in CONFUSION:
            pause = timedelta(seconds=game.rng.uniform(*CONFUSION[trap]))
            Player.objects.filter(pk=bot.pk).update(disabled_until=now + pause)
