# Djangogo clicker — notes for Claude

Realtime multiplayer clicker on Django + Channels. Owner talks in Ukrainian; reply in Ukrainian.

- Game rules: `tasks/game.py` (pure sync functions, the source of truth).
- Bot strategy: `tasks/bot.py`. WebSocket glue: `tasks/consumers.py`. Client: `tasks/static/tasks/js/app.js`.
- Tunables: `GAME` dict in `config/settings.py`.
- Tests: `python manage.py test tasks`. Tests drive time by passing `now=` and switch the
  background loops off with `TICK_SECONDS = 0`.
- Never send `Game.secret`, cards, shields, the rob privilege or session keys in `snapshot()`.

## Owner's core rule (do not break)

- You win by being the first to make `FINISH` (20) of your *own* clicks (deposit + battle; idle burns
  don't count) — or by hitting the hidden winning click.
- The winning click is a maybe: each game rolls its own chance from `SECRET_CHANCE`, and `secret`
  may be `None`. Whether it exists stays secret until the game ends; the rules text must say it
  may or may not exist (an incentive to hunt for it while racing to the finish).
- When it exists it is on the `TARGET_AT` (11..18) click, counted like your own clicks, so it always
  comes before anyone's finish. Re-rolls after a burn stay inside that window (or remove it).
- On every rules change check this still holds; if it's broken or half-done, fix or tune it.

## Remember

- TODO (owner asked, not urgent): there must be a proper standalone description of the game
  rules somewhere (e.g. `docs/RULES.md`), more complete than the short list in `README.md`.
  Until it exists, keep the "Правила" section of `README.md` and the "Як грати" block in
  `tasks/templates/tasks/home.html` in sync with `tasks/game.py` on every rules change.
