"""
Invite-only access: a stranger who finds the server just gets 404.

Make links with `python manage.py invites --count 10 --base https://<your tunnel>`,
send one per person (e.g. in Telegram). Opening <server>/<code> binds that link to the
device's session and lets it in; nothing else is reachable without a valid link.
Switched on by INVITES_REQUIRED (env DJANGO_INVITES=1); off in local development.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponseNotFound
from django.utils import timezone

from .models import Invite

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")
SESSION_FLAG = "invite"


def new_code() -> str:
    return secrets.token_urlsafe(9)  # 12 url-safe chars, ~72 bits: not guessable


def create(count: int, hours: float, note: str = "") -> list[Invite]:
    expires = timezone.now() + timedelta(hours=hours)
    return [Invite.objects.create(code=new_code(), expires_at=expires, note=note) for _ in range(count)]


def redeem(code: str, session_key: str) -> tuple[bool, str]:
    """Bind the invite to this session if there's room. (ok, why-not message)"""
    if not CODE_RE.match(code):
        return False, "Такого запрошення немає"
    invite = Invite.objects.filter(code=code).first()
    if invite is None:
        return False, "Такого запрошення немає"
    if invite.expires_at <= timezone.now():
        return False, "Запрошення вже прострочене — попроси нове"
    if session_key in invite.sessions:
        return True, ""
    if len(invite.sessions) >= settings.INVITE_MAX_DEVICES:
        return False, "Це запрошення вже використали на іншому пристрої"
    invite.sessions = [*invite.sessions, session_key]
    invite.used_at = invite.used_at or timezone.now()
    invite.save(update_fields=["sessions", "used_at"])
    return True, ""


def is_allowed(session) -> bool:
    """Has this session redeemed an invite that is still valid?"""
    if not settings.INVITES_REQUIRED:
        return True
    code, key = session.get(SESSION_FLAG), session.session_key
    if not code or not key:
        return False
    invite = Invite.objects.filter(code=code, expires_at__gt=timezone.now()).first()
    return invite is not None and key in invite.sessions  # SQLite can't do JSON "contains"


class InviteGateMiddleware:
    """Everything except the invite link itself (and static files) needs a redeemed invite."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.INVITES_REQUIRED:
            return self.get_response(request)
        path = request.path_info
        if path.startswith(settings.STATIC_URL) or CODE_RE.match(path.strip("/")):
            return self.get_response(request)
        if is_allowed(request.session):
            return self.get_response(request)
        return not_found()


def not_found(message: str = "Not found"):
    return HttpResponseNotFound(message, content_type="text/plain; charset=utf-8")
