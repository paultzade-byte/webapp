from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render

from . import game, invites


def home(request):
    if not request.session.session_key:
        request.session.save()  # the websocket needs a session cookie to know who you are
    return render(request, "tasks/home.html")


def state(request):
    """Same snapshot the websocket sends — handy for curl and debugging."""
    return JsonResponse(game.snapshot())


def invite(request, code):
    """<server>/<code>: open your personal link once, then you're in."""
    if not settings.INVITES_REQUIRED:
        return redirect("home")
    if not request.session.session_key:
        request.session.save()
    ok, why = invites.redeem(code, request.session.session_key)
    if not ok:
        return invites.not_found(why)
    request.session[invites.SESSION_FLAG] = code
    return redirect("home")
