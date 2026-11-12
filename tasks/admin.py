from django.contrib import admin

from .models import Game, Player


class PlayerInline(admin.TabularInline):
    model = Player
    extra = 0
    readonly_fields = ("public_id", "joined_at", "last_seen")


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("id", "counter", "target", "winner", "created_at", "finished_at")
    inlines = [PlayerInline]


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("name", "game", "clicks", "last_seen")
    list_filter = ("game",)
