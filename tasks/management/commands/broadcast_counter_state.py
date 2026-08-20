import time

from asgiref.sync import async_to_sync
from channels.layers import (
    get_channel_layer,  # pyright: ignore[reportMissingModuleSource]
)
from django.core.management.base import BaseCommand

from tasks.models import GameState


class Command(BaseCommand):
    help = "Broadcasts the counter state to all players"

    def handle(self, *args, **options):
        channel_layer = get_channel_layer()

        while True:
            players = list(
                GameState.objects.values(
                    "id",
                    "counter_value",
                    "is_won",
                    "winner_name"
                )
            )
           #print(f"[JOB] sending {len(players)} players: {players}")

            channel_layer = get_channel_layer()
            assert channel_layer is not None

            async_to_sync(channel_layer.group_send)(
                "game_group",
                {
                    "type": "state.update",
                    "players": players,
                },
            )
            time.sleep(2)
