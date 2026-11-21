"""
All the game rules live here. Plain synchronous functions, no websockets,
no HTTP — so they are easy to read, test and mess around with.
The consumer just calls these and broadcasts the result.

Rules v2 in short:
  * every player gets BUDGET clicks;
  * the first DEPOSIT of them are a deposit: they don't move the shared
    counter, and each one may hit a random trap (slip / modal / lock);
  * after the deposit your clicks move the shared battle counter;
  * the server hides one winning counter value in 1..TARGET_MAX;
    whoever clicks exactly on it wins;
  * no click for IDLE_SECONDS -> -1 click. In battle that click *burns*:
    it moves the counter for you but can't win. Burn the winning one and
    the secret is re-rolled;
  * at CARD_AT personal clicks you get a random card (max one in hand);
  * on a secret random click in ROB_AT you get the 🦹 rob privilege, but only
    if the game has ROB_MIN_PLAYERS+ players. Robbing takes up to ROB clicks
    from anyone (even a sleeper), never all of them.

Every action returns a Result: public events for everyone, private notices
for the actor, and the ids of players whose private state changed.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Game, Player

MAX_NAME_LENGTH = 32
TRAPS = ("slip", "modal", "lock")
CARDS = tuple(Player.Card.values)
TARGETED = {Player.Card.SABOTAGE, Player.Card.SWAP}
BOT_SESSION = "__bot__"  # real Django session keys are 32 random [a-z0-9], so no clash
BOT_NAME = "🤖 Бот"

rng = random.Random()  # tests can seed or patch this


class GameError(Exception):
    """A rule was broken. The message is shown to the player as-is."""


@dataclass
class Result:
    events: list[dict] = field(default_factory=list)  # broadcast to everyone
    notices: list[dict] = field(default_factory=list)  # only to the player who acted
    affected: set[str] = field(default_factory=set)  # public ids whose private state changed


def cfg(key: str):
    return settings.GAME[key]


# ---------------------------------------------------------------- randomness
# Separate tiny functions so tests can patch exactly one decision.

def roll_secret(start: int) -> int:
    return start + rng.randint(1, cfg("TARGET_MAX"))


def roll_trap() -> str | None:
    return rng.choice(TRAPS) if rng.random() < cfg("TRAP_CHANCE") else None


def draw_card() -> str:
    return rng.choice(CARDS)


def roll_rob_at() -> int:
    return rng.randint(*cfg("ROB_AT"))


# ---------------------------------------------------------------- queries

def current_game() -> Game:
    game = Game.objects.order_by("-id").first()
    return game or Game.objects.create(secret=roll_secret(0))


def _lock_current() -> Game:
    return Game.objects.select_for_update().get(pk=current_game().pk)


def find_player(session_key: str | None, game: Game | None = None) -> Player | None:
    if not session_key:
        return None
    game = game or current_game()
    return Player.objects.filter(game=game, session_key=session_key).first()


def in_battle(player: Player) -> bool:
    return player.deposited >= cfg("DEPOSIT")


def can_still_move(player: Player) -> bool:
    return player.budget > 0 or player.card == Player.Card.BATTERY


def is_online(player: Player | None) -> bool:
    if player is None:
        return False
    return timezone.now() - player.last_seen <= timedelta(seconds=cfg("ONLINE_SECONDS"))


def is_stale(game: Game) -> bool:
    last = game.last_click_at or game.created_at
    return timezone.now() - last > timedelta(minutes=cfg("STALE_MINUTES"))


def _ms(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() * 1000))


def snapshot(game: Game | None = None) -> dict:
    """Everything the clients need to draw the screen.
    No session keys, no cards, no shields, and no secret until the game ends!"""
    game = game or current_game()
    game.refresh_from_db()
    deposit = cfg("DEPOSIT")
    players = sorted(
        game.players.all(),
        key=lambda p: (p.deposited < deposit, -p.clicks, -p.deposited, p.joined_at),
    )
    hall = (
        Game.objects.filter(winner__isnull=False)
        .select_related("winner")
        .order_by("-finished_at")[: cfg("HALL_OF_FAME_SIZE")]
    )
    return {
        "game_id": game.pk,
        "counter": game.counter,
        "window": [game.window_start + 1, game.window_start + cfg("TARGET_MAX")],
        "secret": game.secret if game.is_finished else None,
        "finished": game.is_finished,
        "winner": game.winner.name if game.winner else None,
        "starter_id": str(game.starter.public_id) if game.starter else None,
        "with_bot": game.with_bot,
        "rules": {
            k.lower(): cfg(k)
            for k in ("BUDGET", "DEPOSIT", "TARGET_MAX", "IDLE_SECONDS", "BATTERY", "SABOTAGE",
                      "ROB", "ROB_MIN_PLAYERS")
        },
        "players": [
            {
                "id": str(p.public_id),
                "name": p.name,
                "budget": p.budget,
                "deposited": p.deposited,
                "clicks": p.clicks,
                "burned": p.burned,
                "has_card": bool(p.card),
                "clicked": p.last_action_at is not None,  # swap only works on these
                "bot": p.is_bot,
                "online": p.is_bot or is_online(p),
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


def private_state(session_key: str | None, fallback_name: str = "") -> dict:
    """What only you get to know about yourself."""
    now = timezone.now()
    p = find_player(session_key)
    if p is None:
        return {"player_id": None, "name": fallback_name, "card": "", "can_rob": False,
                "shielded": False, "locked_ms": 0, "idle_ms": None}
    return {
        "player_id": str(p.public_id),
        "name": p.name,
        "card": p.card,
        "can_rob": p.can_rob,
        "shielded": p.shielded,
        "locked_ms": _ms(p.disabled_until - now) if p.disabled_until else 0,
        "idle_ms": _ms(now - p.last_action_at) if p.last_action_at else None,
    }


# ---------------------------------------------------------------- idle penalties

def _settle_idle(game: Game, now: datetime, res: Result) -> None:
    """Charge every player for the idle time they've piled up.

    Penalties are computed from timestamps, so it doesn't matter whether this
    runs every second (the ticker) or only when somebody clicks — the result
    is the same. That's why there's no background job to keep in sync.
    """
    if game.is_finished:
        return
    idle = timedelta(seconds=cfg("IDLE_SECONDS"))
    players = Player.objects.filter(
        game=game, budget__gt=0, last_action_at__isnull=False, last_action_at__lte=now - idle
    )
    ticks: list[tuple[datetime, Player]] = []
    for p in players:
        t = p.last_action_at + idle  # pyright: ignore[reportOptionalOperand]
        for _ in range(p.budget + int(p.shielded)):  # no point counting past an empty budget
            if t > now:
                break
            ticks.append((t, p))
            t += idle
    if not ticks:
        return

    ticks.sort(key=lambda tick: tick[0])  # apply in the order they happened
    for t, p in ticks:
        p.last_action_at = t  # keep the remainder: idle 7s = 1 penalty + 2s towards the next one
        if p.budget == 0:
            continue
        pid = str(p.public_id)
        if p.shielded:
            p.shielded = False
            res.affected.add(pid)
            res.events.append({"kind": "shield", "name": p.name, "player_id": pid, "against": "idle"})
            continue
        p.budget -= 1
        if not in_battle(p):
            res.events.append({"kind": "idle", "name": p.name, "player_id": pid})
            continue
        p.burned += 1
        game.counter += 1
        if game.counter == game.secret:
            game.window_start = game.counter
            game.secret = roll_secret(game.counter)
            res.events.append({"kind": "burn_win", "name": p.name, "player_id": pid})
        else:
            res.events.append({"kind": "burn", "name": p.name, "player_id": pid})

    touched = {p.pk: p for _, p in ticks}.values()
    Player.objects.bulk_update(touched, ["last_action_at", "budget", "shielded", "burned"])
    game.save(update_fields=["counter", "secret", "window_start"])


def _check_draw(game: Game, now: datetime, res: Result) -> None:
    if game.is_finished:
        return
    players = list(game.players.all())
    if players and not any(can_still_move(p) for p in players):
        game.finished_at = now
        game.save(update_fields=["finished_at"])
        res.events.append({"kind": "draw"})


def tick(now: datetime | None = None) -> Result:
    """Called by the ticker every TICK_SECONDS."""
    now = now or timezone.now()
    res = Result()
    idle = timedelta(seconds=cfg("IDLE_SECONDS"))
    game = current_game()
    anyone_idle = Player.objects.filter(
        game=game, budget__gt=0, last_action_at__lte=now - idle
    ).exists()
    if game.is_finished or not anyone_idle:
        return res  # cheap exit: don't grab the write lock every second for nothing
    with transaction.atomic():
        game = _lock_current()
        _settle_idle(game, now, res)
        if res.events:
            _check_draw(game, now, res)
    return res


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
        game = _lock_current()
        if game.is_finished:
            raise GameError("Гра вже скінчилась — тисни «Нова гра»")
        existing = Player.objects.filter(game=game, session_key=session_key).first()
        if existing:
            return existing
        if Player.objects.filter(game=game, name_key=name.lower()).exists():
            raise GameError("Таке ім'я вже зайняте, придумай інше...")
        try:
            player = Player.objects.create(
                game=game, session_key=session_key, name=name, name_key=name.lower(),
                budget=cfg("BUDGET"), rob_at=roll_rob_at(),
            )
        except IntegrityError:
            raise GameError("Таке ім'я вже зайняте, придумай інше...")
        if game.starter_id is None:
            game.starter = player
            game.save(update_fields=["starter"])
    return player


def _get_acting_player(game: Game, session_key: str) -> Player:
    player = Player.objects.filter(game=game, session_key=session_key).first()
    if player is None:
        raise GameError("Спочатку збережи своє ім'я!")
    if game.is_finished:
        raise GameError("Гра скінчилась! Чекай на нову 🏁")
    return player


def _milestones(game: Game, player: Player, res: Result) -> None:
    """Goodies for reaching a personal click count."""
    progress = player.deposited + player.clicks
    if progress in cfg("CARD_AT"):
        if player.card:
            res.notices.append({"kind": "card_lost"})
        else:
            player.card = draw_card()
            res.notices.append({"kind": "card", "card": player.card})
    if progress == player.rob_at and not player.can_rob:
        if game.players.count() >= cfg("ROB_MIN_PLAYERS"):
            player.can_rob = True
            res.notices.append({"kind": "privilege"})
        else:  # a teaser: now you know it exists
            res.notices.append({"kind": "privilege_missed", "need": cfg("ROB_MIN_PLAYERS")})


def click(session_key: str, now: datetime | None = None) -> Result:
    now = now or timezone.now()
    res = Result()
    with transaction.atomic():
        game = _lock_current()
        _settle_idle(game, now, res)
        player = _get_acting_player(game, session_key)
        if player.budget == 0:
            raise GameError("Кліки скінчились 🪫 Чекай карту або нову гру")
        if player.disabled_until and now < player.disabled_until:
            res.notices.append({"kind": "locked", "ms": _ms(player.disabled_until - now)})
            return res
        throttle = timedelta(milliseconds=cfg("MIN_CLICK_INTERVAL_MS"))
        if player.last_click_at and now - player.last_click_at < throttle:
            return res  # too fast, silently ignore — autoclickers go home

        pid = str(player.public_id)
        player.last_click_at = player.last_action_at = player.last_seen = now
        game.last_click_at = now

        if not in_battle(player):
            trap = roll_trap()
            if trap:
                res.notices.append({"kind": "trap", "trap": trap, "ms": cfg("LOCK_SECONDS") * 1000})
            if trap != "slip":  # a slipped click simply doesn't count
                player.budget -= 1
                player.deposited += 1
                if trap == "lock":
                    player.disabled_until = now + timedelta(seconds=cfg("LOCK_SECONDS"))
                kind = "armed" if in_battle(player) else "deposit"
                res.events.append({"kind": kind, "name": player.name, "player_id": pid})
                _milestones(game, player, res)
        else:
            player.budget -= 1
            player.clicks += 1
            game.counter += 1
            if game.counter == game.secret:
                game.winner = player
                game.finished_at = now
                res.events.append({"kind": "win", "name": player.name, "player_id": pid})
            else:
                res.events.append({"kind": "click", "name": player.name, "player_id": pid})
            _milestones(game, player, res)

        player.save()
        game.save()
        _check_draw(game, now, res)
    return res


def _revive(player: Player, old_budget: int, now: datetime) -> None:
    """Someone with 0 clicks just got some back: restart their idle timer,
    otherwise they'd instantly pay for all the time they sat at zero."""
    if old_budget == 0 and player.budget > 0 and player.last_action_at:
        player.last_action_at = now


def play_card(session_key: str, target_id: str | None = None, now: datetime | None = None) -> Result:
    now = now or timezone.now()
    res = Result()
    with transaction.atomic():
        game = _lock_current()
        _settle_idle(game, now, res)
        player = _get_acting_player(game, session_key)
        card = player.card
        if not card:
            raise GameError("У тебе немає карти")
        pid = str(player.public_id)
        res.affected.add(pid)
        event = {"kind": "card", "card": card, "name": player.name, "player_id": pid}

        if card in TARGETED:
            target = _find_target(game, target_id)
            if target is None:
                raise GameError("Вибери ціль — клікни по гравцю в списку")
            if target.pk == player.pk:
                raise GameError("Себе саботувати — це вже до психолога 🙃")
            if card == Player.Card.SWAP and target.last_action_at is None:
                raise GameError(f"{target.name} ще жодного разу не клікав — з ним мінятись не можна")
            tid = str(target.public_id)
            event.update(target=target.name, target_id=tid)
            res.affected.add(tid)
            my_old, their_old = player.budget, target.budget
            if target.shielded:
                target.shielded = False
                res.events.append({**event, "kind": "shield", "against": card})
            elif card == Player.Card.SABOTAGE:
                target.budget = max(0, target.budget - cfg("SABOTAGE"))
                res.events.append(event)
            else:  # swap
                player.budget, target.budget = target.budget, player.budget
                res.events.append(event)
            _revive(player, my_old, now)
            _revive(target, their_old, now)
            target.save()
        elif card == Player.Card.BATTERY:
            old = player.budget
            player.budget += cfg("BATTERY")
            _revive(player, old, now)
            res.events.append(event)
        else:  # shield: secret on purpose, nobody else is told
            player.shielded = True
            res.notices.append({"kind": "shield_up"})

        player.card = ""
        player.save()
        _check_draw(game, now, res)
    return res


def rob(session_key: str, target_id: str | None, now: datetime | None = None) -> Result:
    """🦹 Take up to ROB clicks from anyone, even from someone who never clicked.
    The victim always keeps at least one click: no robbing to the bone."""
    now = now or timezone.now()
    res = Result()
    with transaction.atomic():
        game = _lock_current()
        _settle_idle(game, now, res)
        player = _get_acting_player(game, session_key)
        if not player.can_rob:
            raise GameError("У тебе немає привілею на грабунок 🦹")
        if game.players.count() < cfg("ROB_MIN_PLAYERS"):
            raise GameError(f"Грабувати можна, коли в грі {cfg('ROB_MIN_PLAYERS')}+ гравців")
        target = _find_target(game, target_id)
        if target is None:
            raise GameError("Вибери, кого грабуємо — клікни по гравцю в списку")
        if target.pk == player.pk:
            raise GameError("Пограбувати себе? Сміливо, але ні 🙃")
        loot = min(cfg("ROB"), target.budget - 1)
        if loot <= 0:
            raise GameError(f"У {target.name} і так останній клік — нічого брати")

        pid, tid = str(player.public_id), str(target.public_id)
        res.affected |= {pid, tid}
        event = {"kind": "rob", "name": player.name, "player_id": pid,
                 "target": target.name, "target_id": tid}
        if target.shielded:
            target.shielded = False
            res.events.append({**event, "kind": "shield", "against": "rob"})
        else:
            old = player.budget
            target.budget -= loot
            player.budget += loot
            _revive(player, old, now)
            res.events.append({**event, "amount": loot})
        player.can_rob = False
        player.save()
        target.save()
        _check_draw(game, now, res)
    return res


def _find_target(game: Game, target_id: str | None) -> Player | None:
    try:
        public_id = uuid.UUID(str(target_id))
    except ValueError:
        return None
    return Player.objects.filter(game=game, public_id=public_id).first()


# ---------------------------------------------------------------- the bot toggle
# What the bot *does* lives in bot.py. Here are only the rules of having one.

def _spawn_bot(game: Game) -> Player:
    name, n = BOT_NAME, 1
    while Player.objects.filter(game=game, name_key=name.lower()).exists():
        n += 1
        name = f"{BOT_NAME} {n}"  # somebody already called themselves "🤖 Бот", cute
    return Player.objects.create(
        game=game, session_key=BOT_SESSION, name=name, name_key=name.lower(),
        budget=cfg("BUDGET"), is_bot=True, rob_at=roll_rob_at(),
    )


def set_bot(session_key: str, enabled: bool) -> Result:
    """Same permission as a restart: the starter, or anyone if the starter is gone."""
    res = Result()
    with transaction.atomic():
        game = _lock_current()
        player = _get_acting_player(game, session_key)
        if not can_restart(session_key, game):
            raise GameError("Бота кличе тільки той, хто розпалив гру 🔥")
        bot = Player.objects.filter(game=game, is_bot=True).first()
        if enabled and bot is None:
            _spawn_bot(game)
            res.events.append({"kind": "bot_on", "by": player.name})
        elif not enabled and bot is not None:
            bot.delete()  # its clicks stay in the counter, like footprints
            res.events.append({"kind": "bot_off", "by": player.name})
        game.with_bot = enabled
        game.save(update_fields=["with_bot"])
    return res


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
        game = _lock_current()
        if not can_restart(session_key, game):
            raise GameError("Нову гру може почати тільки той, хто розпалив цю 🔥")
        if game.last_click_at is None and not game.is_finished:
            return game  # nobody clicked yet, no point in a new row
        if not game.is_finished:
            game.finished_at = timezone.now()
            game.save(update_fields=["finished_at"])
        fresh = Game.objects.create(secret=roll_secret(0), with_bot=game.with_bot)
        if fresh.with_bot:
            _spawn_bot(fresh)
        return fresh


def touch(session_key: str | None) -> None:
    """Heartbeat: keeps the player 'online'. Does NOT reset the idle timer —
    only real clicks do that."""
    if session_key:
        Player.objects.filter(game=current_game(), session_key=session_key).update(
            last_seen=timezone.now()
        )
