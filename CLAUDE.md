# Djangogo clicker — notes for Claude

Realtime multiplayer clicker on Django + Channels. Owner talks in Ukrainian; reply in Ukrainian.

- Game rules: `tasks/game.py` (pure sync functions, the source of truth).
- Bot strategy: `tasks/bot.py`. WebSocket glue: `tasks/consumers.py`. Client: `tasks/static/tasks/js/app.js`.
- Tunables: `GAME` dict in `config/settings.py`.
- Tests: `python manage.py test tasks`. Tests drive time by passing `now=` and switch the
  background loops off with `TICK_SECONDS = 0`.
- Never send `Game.secret`, cards, shields, the rob privilege or session keys in `snapshot()`.

## Remember

- TODO (owner asked, not urgent): there must be a proper standalone description of the game
  rules somewhere (e.g. `docs/RULES.md`), more complete than the short list in `README.md`.
  Until it exists, keep the "Правила" section of `README.md` and the "Як грати" block in
  `tasks/templates/tasks/home.html` in sync with `tasks/game.py` on every rules change.
