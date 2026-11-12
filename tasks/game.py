"""
All the game rules live here. Plain synchronous functions, no websockets,
no HTTP — so they are easy to read, test and mess around with.
The consumer just calls these and broadcasts the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import Game, Player

MAX_NAME_LENGTH = 32


class GameError(Exception):
    """A rule was broken. The message is shown to the player as-is."""


def cfg(key: str):
    return settings.GAME[key]


# ---------------------------------------------------------------- queries

def current_game() -> Game:
    game = Game.objects.order_by("-id").first()
    if game is None:
        game = Game.objects.create(target=cfg("TARGET"))
    return game


def find_player(session_key: str | None, game: Game | None = None) -> Player | None:
    if not session_key:
        return None
    game = game or current_game()
    return Player.objects.filter(game=game, session_key=session_key).first()


def is_online(player: Player | None) -> bool:
    if player is None:
        return False
    return timezone.now() - player.last_seen <= timedelta(seconds=cfg("ONLINE_SECONDS"))


def is_stale(game: Game) -> bool:
    last = game.last_click_at or game.created_at
    return timezone.now() - last > timedelta(minutes=cfg("STALE_MINUTES"))


def snapshot(game: Game | None = None) -> dict:
    """Everything the clients need to draw the screen. No session keys inside!"""
    game = game or current_game()
    game.refresh_from_db()
    players = list(game.players.order_by("-clicks", "joined_at"))
    hall = (
        Game.objects.filter(winner__isnull=False)
        .select_related("winner")
        .order_by("-finished_at")[: cfg("HALL_OF_FAME_SIZE")]
    )
    return {
        "game_id": game.pk,
        "counter": game.counter,
        "target": game.target,
        "finished": game.is_finished,
        "winner": game.winner.name if game.winner else None,
        "starter_id": str(game.starter.public_id) if game.starter else None,
        "players": [
            {
                "id": str(p.public_id),
                "name": p.name,
                "clicks": p.clicks,
                "online": is_online(p),
            }
            for p in players
        ],
        "hall_of_fame": [
            {
                "name": g.winner.name,  # pyright: ignore[reportOptionalMemberAccess]
                "game_id": g.pk,
                "seconds": round((g.finished_at - g.created_at).total_seconds()),  # pyright: ignore
            }
            for g in hall
        ],
    }


# ---------------------------------------------------------------- actions

def clean_name(raw: str) -> str:
    name = " ".join((raw or "").split())
    if not name:
        raise GameError("Спочатку введи ім'я 🙃")
    if len(name) > MAX_NAME_LENGTH:
        raise GameError(f"Ім'я задовге, максимум {MAX_NAME_LENGTH} символи")
    return name


def join(session_key: str, raw_name: str) -> Player:
    name = clean_name(raw_name)
    with transaction.atomic():
        game = Game.objects.select_for_update().get(pk=current_game().pk)
        if game.is_finished:
            raise GameError("Гра вже скінчилась — тисни «Нова гра»")
        existing = Player.objects.filter(game=game, session_key=session_key).first()
        if existing:
            return existing
        if Player.objects.filter(game=game, name_key=name.lower()).exists():
            raise GameError("Таке ім'я вже зайняте, придумай інше...")
        try:
            player = Player.objects.create(
                game=game, session_key=session_key, name=name, name_key=name.lower()
            )
        except IntegrityError:
            raise GameError("Таке ім'я вже зайняте, придумай інше...")
        if game.starter_id is None:
            game.starter = player
            game.save(update_fields=["starter"])
    return player


@dataclass
class ClickResult:
    counter: int
    target: int
    won: bool
    player: Player


def click(session_key: str) -> ClickResult:
    with transaction.atomic():
        game = Game.objects.select_for_update().get(pk=current_game().pk)
        player = find_player(session_key, game)
        if player is None:
            raise GameError("Спочатку збережи своє ім'я!")
        if game.is_finished:
            raise GameError("Гра скінчилась! Чекай на нову 🏁")

        now = timezone.now()
        game.counter = F("counter") + 1
        game.last_click_at = now
        game.save(update_fields=["counter", "last_click_at"])
        game.refresh_from_db(fields=["counter"])

        won = game.counter >= game.target
        if won:
            game.winner = player
            game.finished_at = now
            game.save(update_fields=["winner", "finished_at"])

        Player.objects.filter(pk=player.pk).update(clicks=F("clicks") + 1, last_seen=now)
        player.refresh_from_db()
    return ClickResult(counter=game.counter, target=game.target, won=won, player=player)


def can_restart(session_key: str | None, game: Game) -> bool:
    """While a game is running, only its starter may reset it —
    unless the starter vanished or nobody has clicked for a while."""
    if game.is_finished or game.starter is None or is_stale(game):
        return True
    if not is_online(game.starter):
        return True
    return game.starter.session_key == session_key


def new_game(session_key: str | None) -> Game:
    with transaction.atomic():
        game = Game.objects.select_for_update().get(pk=current_game().pk)
        if not can_restart(session_key, game):
            raise GameError("Нову гру може почати тільки той, хто розпалив цю 🔥")
        if game.counter == 0 and not game.is_finished:
            return game  # nothing happened yet, no point in a new row
        if not game.is_finished:
            game.finished_at = timezone.now()
            game.save(update_fields=["finished_at"])
        return Game.objects.create(target=cfg("TARGET"))


def touch(session_key: str | None) -> None:
    """Heartbeat: keeps the player 'online'."""
    if session_key:
        Player.objects.filter(game=current_game(), session_key=session_key).update(
            last_seen=timezone.now()
        )
