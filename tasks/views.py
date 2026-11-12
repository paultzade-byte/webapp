from django.http import JsonResponse
from django.shortcuts import render

from . import game


def home(request):
    if not request.session.session_key:
        request.session.save()  # the websocket needs a session cookie to know who you are
    return render(request, "tasks/home.html")


def state(request):
    """Same snapshot the websocket sends — handy for curl and debugging."""
    return JsonResponse(game.snapshot())
