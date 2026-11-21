from django.contrib import admin

from .models import Game, Invite, Player


class PlayerInline(admin.TabularInline):
    model = Player
    extra = 0
    readonly_fields = ("public_id", "joined_at", "last_seen")


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("id", "counter", "secret", "winner", "created_at", "finished_at")
    inlines = [PlayerInline]


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("name", "game", "budget", "deposited", "clicks", "card", "last_seen")
    list_filter = ("game",)


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ("code", "note", "created_at", "expires_at", "used_at")
    readonly_fields = ("sessions",)
