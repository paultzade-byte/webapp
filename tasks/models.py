import uuid

from django.db import models


class GameState(models.Model):
    counter_value = models.IntegerField(default=0)
    winner_name = models.CharField(max_length=100, blank=True, null=True)
    is_won = models.BooleanField(default=False)
    starter_session_key = models.CharField(max_length=40, blank=True, null=True)
    last_click_at = models.DateTimeField(auto_now=True)
    guid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    def __str__(self):
        return f"Counter: {self.counter_value}, Winner: {self.winner_name or '—'}"


class Player(models.Model):
    user_name = models.CharField(max_length=100)
    session_key = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user_name
