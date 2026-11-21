import uuid

from django.db import models


class Game(models.Model):
    """One round. The newest row is always the current game."""

    counter = models.PositiveIntegerField(default=0)  # battle clicks only (deposits don't count)
    secret = models.PositiveIntegerField(default=1)  # the winning counter value. NEVER send it to clients!
    window_start = models.PositiveIntegerField(default=0)  # secret is in window_start+1 .. window_start+TARGET_MAX
    with_bot = models.BooleanField(default=False)  # the "гра з ботом" toggle; new games inherit it
    starter = models.ForeignKey(
        "Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    winner = models.ForeignKey(
        "Player", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_click_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None

    def __str__(self):
        return f"Game #{self.pk}: {self.counter} (secret {self.secret})"


class Player(models.Model):
    class Card(models.TextChoices):
        BATTERY = "battery", "🔋 Батарейка"
        SABOTAGE = "sabotage", "🗡 Саботаж"
        SWAP = "swap", "🔄 Обмін"
        SHIELD = "shield", "🛡 Щит"

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="players")
    session_key = models.CharField(max_length=40)
    name = models.CharField(max_length=32)
    name_key = models.CharField(max_length=32)  # lowercased, for uniqueness
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    is_bot = models.BooleanField(default=False)

    budget = models.PositiveSmallIntegerField(default=20)  # clicks left
    deposited = models.PositiveSmallIntegerField(default=0)  # deposit progress
    clicks = models.PositiveSmallIntegerField(default=0)  # battle clicks made
    burned = models.PositiveSmallIntegerField(default=0)  # battle clicks lost to idling
    card = models.CharField(max_length=16, choices=Card.choices, blank=True)
    shielded = models.BooleanField(default=False)
    disabled_until = models.DateTimeField(null=True, blank=True)  # "lock" trap
    rob_at = models.PositiveSmallIntegerField(null=True, blank=True)  # secret click that drops the 🦹 privilege
    can_rob = models.BooleanField(default=False)  # holds the 🦹 privilege right now

    last_action_at = models.DateTimeField(null=True, blank=True)  # idle timer; None = never clicked
    last_click_at = models.DateTimeField(null=True, blank=True)  # throttle
    joined_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["game", "session_key"], name="one_player_per_session"),
            models.UniqueConstraint(fields=["game", "name_key"], name="unique_name_per_game"),
        ]

    def __str__(self):
        return self.name
