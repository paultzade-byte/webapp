# Halo Djangogo! — антизлісний клікер

Спільний realtime-клікер: всі гравці разом клікають до цілі (за замовчуванням 100),
хто зробить останній клік — той переміг і потрапляє в зал слави.

## Запуск

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Redis не обов'язковий. Без нього працює in-memory channel layer (тільки для одного процесу).
Щоб підключити Redis: `REDIS_URL=redis://127.0.0.1:6379 python manage.py runserver`.

Інші змінні: `GAME_TARGET`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`.

Тести: `python manage.py test tasks`

## Як це влаштовано

| Файл | Що там |
|---|---|
| `tasks/game.py` | **Всі правила гри.** Звичайні sync-функції: `join`, `click`, `new_game`, `snapshot`. Почни звідси. |
| `tasks/consumers.py` | WebSocket: приймає дії, викликає `game.*`, розсилає стан усім. Протокол описано зверху файлу. |
| `tasks/models.py` | `Game` (раунд; найновіший = поточний) і `Player` (гравець у конкретному раунді). |
| `tasks/static/tasks/js/app.js` | Весь клієнт. Сервер — джерело правди, клієнт тільки малює. |
| `config/settings.py` → `GAME` | Крутилки: ціль, таймаути, антиавтоклікер. |

`GET /api/state/` повертає той самий стан, що й WebSocket — зручно для `curl`.

## Ідеї, що можна покрутити

- Пауери: «x2 на 10 секунд» за кожні 10 кліків поспіль.
- Саботаж: кнопка «-5» для того, хто лідирує 😈
- Кімнати: `ws/game/<room>/` і окремий `Game` на кімнату.
- Ачівки («перший клік», «комбо x50», «програв на 99»).
- Кнопка, яка втікає щоразу, коли лічильник кратний 13.
