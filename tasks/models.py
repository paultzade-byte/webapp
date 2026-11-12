import uuid

from django.db import models


class Game(models.Model):
    """One round of the clicker. The newest row is always the current game."""

    target = models.PositiveIntegerField(default=100)
    counter = models.PositiveIntegerField(default=0)
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
        return f"Game #{self.pk}: {self.counter}/{self.target}"


class Player(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="players")
    session_key = models.CharField(max_length=40)
    name = models.CharField(max_length=32)
    name_key = models.CharField(max_length=32)  # lowercased, for uniqueness
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    clicks = models.PositiveIntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["game", "session_key"], name="one_player_per_session"),
            models.UniqueConstraint(fields=["game", "name_key"], name="unique_name_per_game"),
        ]

    def __str__(self):
        return self.name
