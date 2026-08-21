from django.shortcuts import render
from tasks.models import GameState, Player
from django.http import JsonResponse
from django.contrib.sessions.models import Session
from django.utils import timezone
from datetime import timedelta

STARTER_TIMEOUT_MINUTES = 5
WIN_THRESHOLD = 20

def is_session_alive(session_key):
    if not session_key:
        return False
    return Session.objects.filter(
        session_key=session_key,
        expire_date__gte=timezone.now(),
    ).exists()

def get_game_state():
    state, _ = GameState.objects.get_or_create(pk=1)
    return state

def home(request):
    if not request.session.session_key:
       request.session.save()   # create a session key if one doesn't exist

    state = get_game_state()
    is_starter = state.starter_session_key == request.session.session_key
    name_saved = bool(request.session.get("user_name"))
    saved_name = request.session.get("user_name", "")


    return render(request, "tasks/home.html", {
        "counter": state.counter_value,
        "is_starter": is_starter,
        "name_saved": name_saved,
        "saved_name": saved_name,
        "game_started": state.counter_value > 0, # flag for game state to keep button on the same place after refresh
    })

def save_name(request):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)

    name = request.POST.get("user_name", "").strip()
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)

    if not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key

    taken = Player.objects.filter(user_name__iexact=name).exclude(session_key=session_key).exists()
    if taken:
        return JsonResponse({"error": "This name already exists, take another one..."}, status=400)

    Player.objects.update_or_create(
        session_key=session_key,
        defaults={"user_name": name},
    )

    state = get_game_state()
    is_first_player = not state.starter_session_key
    if is_first_player:
        state.starter_session_key = session_key
        state.save()

    request.session["user_name"] = name

    return JsonResponse({
        "status": "ok",
        "user_name": name,
        "is_starter": state.starter_session_key == session_key,
    })

def click(request):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)

    name = request.session.get("user_name")
    if not name:
        return JsonResponse({"error": "First save your f'...' name!!!"}, status=400)

    state = get_game_state()
    if state.is_won:
        return JsonResponse({"error": "Game over! Wait for the next one!"}, status=400)

    state.counter_value += 1
    if state.counter_value >= WIN_THRESHOLD:
        state.is_won = True
        state.winner_name = name
    state.save()

    return JsonResponse({
        "status": "ok",
        "counter_value": state.counter_value,
        "is_won":state.is_won,
        "winner_name": state.winner_name,
    })

def new_game(request):
    if request.method != "POST":
        return JsonResponse({"error": "nethod not allowed"}, status=405)

    state = get_game_state()
    print(f"[DEBUG] cookies: {request.COOKIES}")
    is_stale = timezone.now() - state.last_click_at > timedelta(minutes=STARTER_TIMEOUT_MINUTES)
    print(f"[DEBUG] my session: {request.session.session_key}, starter: {state.starter_session_key}, is_won: {state.is_won}, is_stale: {is_stale}")

    starter_is_alive = is_session_alive(state.starter_session_key)

    if not state.is_won and not is_stale and starter_is_alive and state.starter_session_key != request.session.session_key:
        return JsonResponse({"error": "Only firestarter could start new game while it lasts"}, status=403)

    state.counter_value = 0
    state.is_won = False
    state.winner_name = None
    state.starter_session_key = None
    state.save()

    Player.objects.all().delete()

    return JsonResponse({"status": "ok"})
