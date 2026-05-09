# Saver Backend

Бэкенд Telegram-бота, который скачивает медиа с большого числа социальных платформ и пересылает его прямо внутри Telegram. В основе — FastAPI + aiogram 3 + Taskiq + yt-dlp, плюс self-hosted Telegram Bot API сервер, провайдер PO-токенов для YouTube и headless Chrome для сайтов, требующих живой браузер.

[🇬🇧 English version](README.md)

## 📋 Оглавление

- [Возможности](#-возможности)
- [Поддерживаемые платформы](#-поддерживаемые-платформы)
- [Архитектура](#-архитектура)
- [Требования](#-требования)
- [Быстрый старт](#-быстрый-старт)
- [Конфигурация](#-конфигурация)
- [Разработка](#-разработка)
- [Деплой](#-деплой)
- [Структура проекта](#-структура-проекта)
- [Переменные окружения](#-переменные-окружения)
- [Качество кода](#-качество-кода)
- [Вклад](#-вклад)
- [Лицензия](#-лицензия)

## ✨ Возможности

- **18+ поддерживаемых платформ** — видео, фото-карусели, аудио (полный список ниже).
- **Бот работает только через webhook** с self-hosted [aiogram/telegram-bot-api](https://github.com/aiogram/telegram-bot-api), что позволяет отдавать файлы до 2 ГБ.
- **Фоновый пайплайн** на [Taskiq](https://taskiq-python.github.io/) поверх Redis: progress-сообщения, тайм-ауты на каждую задачу.
- **Двухпроцессная архитектура бота** — FastAPI-приложение (приёмник вебхука) и Taskiq-воркер каждый держат собственный экземпляр `Bot` и шарят FSM-стор в Redis.
- **Выбор качества и языка** для длинных видео (YouTube, VK Video, Rutube, OK.ru, M3U8, Kinovod) с превью-обложкой.
- **Мастер фильмов и сериалов** ("VideoTheatre") — пошаговая клавиатура «сезон → серия → озвучка».
- **Inline-режим** — TikTok и Instagram прямо из любого чата через `@botname <url>`. Пустой inline-запрос показывает последние 20 кэшированных коротких видео пользователя как личную галерею.
- **Кэш `file_id`** в PostgreSQL — повторные скачивания того же видео мгновенные, ничего не перезаливается.
- **Шлюз подписок** — бот может требовать членства в указанных каналах перед скачиванием.
- **i18n** на английском и русском с автовыбором по локали Telegram-клиента (`SimpleI18nMiddleware` от aiogram).
- **Команда `/stats`** для админа — KPI-отчёт по запросу (новые/активные пользователи, скачивания, разбивка по источникам).
- **Периодическая очистка** старых файлов (Taskiq cron, каждые 10 минут).
- **Sentry** (опционально) — интеграции с FastAPI / SQLAlchemy / Redis / stdlib предзаведены.
- **Все нераспознанные URL** автоматически пересылаются в админ-чат для разбора.

## 🌐 Поддерживаемые платформы

Резолвер автоматически определяет источник по host'у и path'у URL. Каждой платформе соответствует отдельный контроллер в `saver_backend/services/downloaders/`.

| Платформа | Бэкенд | Аутентификация | Заметки |
|---|---|---|---|
| YouTube (видео) | yt-dlp + aria2c + bgutil POT provider | cookies | выбор качества |
| YouTube Shorts | yt-dlp + bgutil POT provider | cookies | прямое скачивание |
| Instagram (reels, posts, карусели) | scraper indown.io | нет | основной обработчик `/p/` и `/reel(s)/` |
| Instagram (stories) | [`instaloader`](https://instaloader.github.io/) | session-файл аккаунта | |
| TikTok | API `tikwm.com` | нет | видео и фото-слайдшоу |
| X (Twitter) | yt-dlp + fallback на `fixupx.com` | нет | |
| VK Video | yt-dlp через RU-прокси | нет | выбор качества |
| VK Clips | yt-dlp через RU-прокси | нет | |
| VK посты на стене и фото | [`vkbottle`](https://github.com/vkbottle/vkbottle) | VK service-токен(ы) | смешанная медиа (фото + видео + аудио) |
| Rutube | yt-dlp через RU-прокси | нет | выбор качества |
| OK.ru | yt-dlp через RU-прокси | нет | выбор качества |
| Pinterest | yt-dlp | нет | |
| Дзен | yt-dlp | нет | |
| Reddit | yt-dlp + aria2c | cookies опционально | видео и анимированные GIF |
| Facebook | yt-dlp через прокси | нет | fb.com / m.fb.com / fb.watch |
| Kinovod | Playwright + headless Chrome через CDP + патч playerjs | нужен RU-прокси | полные фильмы и сериалы с мастером сезонов/серий |
| Yandex Music | [`ymdantic`](https://pypi.org/project/ymdantic/) | YM-токен | треки и альбомы целиком (аудио) |
| M3U8 потоки | yt-dlp | нет | любой URL, заканчивающийся на `.m3u8` |
| Adult-сайты | yt-dlp | cookies опционально | список хостов в `adult_ydl_source.py` |

## 🏗️ Архитектура

Деплой полностью контейнеризован. Один `docker-compose.yml` поднимает десять сервисов:

| Сервис | Образ | Роль |
|---|---|---|
| `api` | собирается из `Dockerfile` | FastAPI-приложение — принимает Telegram webhook, отдаёт `/api/health` |
| `taskiq-worker` | тот же образ | выполняет пайплайн скачиваний (5 ГБ / 3 CPU) |
| `taskiq-scheduler` | тот же образ | cron-планировщик (cleanup) |
| `migrator` | тот же образ | разовый запуск `alembic upgrade head` |
| `db` | `postgres:16.3-bullseye` | основная БД |
| `redis` | `valkey/valkey:8-alpine` | брокер Taskiq (DB 1) + FSM-стор aiogram. Valkey — BSD-3 OSS-форк Redis под Linux Foundation, drop-in совместимый |
| `nginx` | `nginx:1.28-trixie` | TLS-терминация, обратный прокси к `api` |
| `telegram-bot-api` | `aiogram/telegram-bot-api:9.6` | self-hosted Bot API (для отдачи файлов до 2 ГБ) |
| `bgutil` | `brainicism/bgutil-ytdlp-pot-provider:1.3.0` | провайдер PO-токенов для yt-dlp/YouTube |
| `chrome` | `chromedp/headless-shell:148.0.7778.97` | headless Chrome через CDP для источника Kinovod (Playwright) |

Поток данных:

```
Telegram → nginx (TLS) → api (FastAPI webhook)
                          │
                          ├─ aiogram dispatcher (FSM в Redis DB 0)
                          │
                          └─ Taskiq broker (Redis DB 1)
                                │
                                └─ taskiq-worker
                                     ├─ SourceResolver → контроллер платформы (yt-dlp / indown / vkbottle / …)
                                     ├─ self-hosted telegram-bot-api → пользователь (файл загружается один раз, file_id кэшируется)
                                     └─ PostgreSQL (users, history, кэш file_id)

taskiq-scheduler ── cron */10 * * * * ── cleanup_old_files_task → ./downloads
```

Бот работает **только через webhook** — fallback на polling нет. Telegram должен иметь возможность достучаться до `https://<your-domain>/api/webhook/telegram/<bot-token>`.

## 📦 Требования

- **Python 3.14** (`pyproject.toml` требует `>=3.14,<4.0`)
- [`uv`](https://github.com/astral-sh/uv) `0.11+` для управления зависимостями
- [Task](https://taskfile.dev/) для shortcut-команд
- Docker Engine + Docker Compose v2
- Для zero-downtime прод-деплоя: плагин [`docker rollout`](https://github.com/Wowu/docker-rollout) на сервере
- Bot-токен от [@BotFather](https://t.me/BotFather)
- **Telegram API ID и API hash** с [my.telegram.org](https://my.telegram.org/apps) — нужны для контейнера self-hosted Bot API
- Публичный HTTPS-домен на ваш nginx (Telegram отказывается слать webhook на HTTP)
- Опционально: Sentry DSN, VK service-токен, токен Yandex Music, аккаунт Instagram, RU SOCKS-прокси

## 🚀 Быстрый старт

```bash
# 1. Клонирование
git clone https://github.com/<your-account>/saver_backend.git
cd saver_backend

# 2. Зависимости и pre-commit хуки
uv sync --all-groups
uv run pre-commit install

# 3. Конфигурация
cp .env.example .env
$EDITOR .env   # заполнить токены, см. "Переменные окружения" ниже

# 4. Поднять локальный стек
task build-local
task deploy-local
```

Локальные оверрайды (`deploy/docker-compose.dev.yml`) пробрасывают Postgres на `5432`, Redis на `6379`, API на `8000`, self-hosted Bot API на `8081/8082`, bgutil на `4416`, монтируют исходники с `--reload` для `api` и обоих Taskiq-процессов.

Чтобы Telegram мог зарегистрировать webhook, бот должен быть доступен снаружи. Локально это обычно туннель ([`ngrok`](https://ngrok.com/), [`cloudflared`](https://github.com/cloudflare/cloudflared)) перед API + соответствующий `SAVER_BACKEND_WEBHOOK_BASE_URL`.

## ⚙️ Конфигурация

### Файл `.env`

Все настройки приложения грузятся из `.env` через `pydantic-settings` с префиксом `SAVER_BACKEND_`. Полный список см. в [Переменных окружения](#-переменные-окружения). Контейнер Telegram Bot API дополнительно читает две переменные **без префикса** — `TELEGRAM_API_ID` и `TELEGRAM_API_HASH`.

### Host UID matching

Прод-стадия Docker запускается под non-root пользователем с UID/GID `1000` по умолчанию. Чтобы bind-mount `./cookies` и `./downloads` был writable из контейнера без хостового `chown`, host-owner этих папок должен совпадать. Если у твоего deploy-юзера другой UID/GID, задай `APP_UID` / `APP_GID` в `.env`:

```bash
echo "APP_UID=$(id -u)" >> .env
echo "APP_GID=$(id -g)" >> .env
```

Следующий `task deploy` пересоберёт образ с этими ID, запеченными в `app`-пользователе. На свежих хостах, где папок ещё нет, просто `mkdir -p downloads cookies` под deploy-юзером.

### Cookies

Часть источников требует cookies для обхода анти-бот-проверок. Кладите Netscape-файлы `cookies*.txt` в подпапки `cookies/`:

| Папка | Используется | Обязательно? |
|---|---|---|
| `cookies/youtube_video_ydl/` | YouTube видео | да |
| `cookies/youtube_shorts_ydl/` | YouTube Shorts | да |
| `cookies/vk_api_ydl/` | VK API (стена + фото) | опционально |
| `cookies/adult_ydl/` | Adult-источник | опционально |
| `cookies/instagram_instaloader/` | Instagram stories (файл `<login>.session`) | да |

Дерево `cookies/*` целиком в `.gitignore`. yt-dlp-загрузчик случайно выбирает один файл из доступных на каждый запрос — можно ротировать cookies, добавляя файлы в папку.

### Сессия Instagram

Источнику `instaloader` для stories нужен сохранённый session-файл:

1. Задайте `SAVER_BACKEND_INSTAGRAM_ACCOUNT="login:password"` в `.env`.
2. Положите Netscape-экспорт cookies того же аккаунта в `cookies/instagram_instaloader/<login>.txt`.
3. Запустите `uv run python scripts/create_instagram_session.py` — скрипт проверит cookies, вызовет `loader.test_login()` и запишет `cookies/instagram_instaloader/<login>.session`.

### Миграции БД

```bash
task migrate                    # через docker compose
uv run alembic upgrade head     # вручную
```

В `saver_backend/db/migrations/versions/` лежат 5 миграций. Compose-сервис `migrator` один раз выполняет `alembic upgrade head` и завершается.

## 💻 Разработка

### Команды Taskfile

Проект использует [Taskfile](https://taskfile.dev/) для shortcut-команд:

| Команда | Описание |
|---|---|
| `task build-local` | Собрать образы по `docker-compose.yml` + `deploy/docker-compose.dev.yml` |
| `task deploy-local` | Поднять dev-стек с авто-релоадом |
| `task migrate` | Собрать + запустить сервис `migrator` |
| `task locales` | extract → update → compile `.po` (с `DEBUG=1 task locales` сохраняются source-locations) |
| `task deploy` | Прод-деплой (zero-downtime через `docker rollout`, если стек уже запущен) |

### Локализация

Переводы лежат в `locales/{en,ru}/LC_MESSAGES/messages.po` и компилируются в `messages.mo` при старте каждого контейнера (все entrypoint-скрипты вызывают `compile_po.sh`). Добавление/обновление строк:

```bash
task locales
```

Под капотом — `pybabel extract` → `pybabel update` → `pybabel compile`. Дополнительно есть workflow Tolgee (`.github/workflows/tolgee_push.yml`), который синхронизирует новые ключи в Tolgee на каждый push в `dev`/`main`.

### Тесты

```bash
uv run pytest -vv
# или внутри docker-стека:
docker compose run --rm api pytest -vv
```

CI запускает `pytest` внутри собранного `dev`-таргета Dockerfile (`.github/workflows/tests.yml`).

## 🚢 Деплой

### Прод-деплой через Taskfile

```bash
task deploy
```

Команда выбирает одну из двух стратегий:

- **Холодный старт** — если `api` и `taskiq-worker` не запущены, делает `up --build -d` для всего стека.
- **Скользящее обновление** — иначе пересобирает образы, делает `docker rollout api`, запускает `migrator`, перезагружает nginx, потом `docker rollout taskiq-worker`. Требует плагин [`docker rollout`](https://github.com/Wowu/docker-rollout) на хосте.

### GitHub Actions

В `.github/workflows/` три workflow:

| Workflow | Триггер | Что делает |
|---|---|---|
| `tests.yml` | каждый push | Lint-матрица (black / ruff / mypy через pre-commit) + dockerised `pytest` |
| `deploy.yml` | успешный `tests.yml` на `dev`/`main` или вручную | SSH-ится на сервер, тянет коммит через GitHub App-токен, пишет `.env` из `secrets.ENV_FILE`, выполняет `task deploy` |
| `tolgee_push.yml` | push в `main`/`dev` или вручную | Пушит новые ключи переводов в Tolgee |

Что нужно настроить в GitHub:

- **Repository variable**: `APP_ID` (ID GitHub-приложения для деплой-токена)
- **Environment secrets** (для окружений `dev` и `prod`):
  - `APP_PRIVATE_KEY` — приватный ключ GitHub-приложения
  - `SSH_HOST`, `SSH_USER`, `SSH_KEY`, `SSH_PORT`
  - `ENV_FILE` — содержимое `.env`, которое пишется на сервер на каждый деплой
- **Tolgee secrets** (на уровне репозитория): `TOLGEE_API_URL`, `TOLGEE_API_KEY`, `TOLGEE_PROJECT_ID`

## 📁 Структура проекта

```
saver_backend/
├── saver_backend/                 # Основной пакет приложения
│   ├── __main__.py                # точка входа (uvicorn в reload или gunicorn)
│   ├── gunicorn_runner.py         # кастомный UvicornWorker для gunicorn
│   ├── settings.py                # все SAVER_BACKEND_* переменные окружения
│   ├── log.py                     # конфиг loguru
│   ├── tkq.py                     # брокер и планировщик Taskiq, FastAPI-интеграция
│   ├── db/                        # слой SQLAlchemy 2.0
│   │   ├── models/                # users, history, cache (хранилище file_id)
│   │   ├── dao/                   # user / history / cache DAO
│   │   ├── migrations/            # alembic
│   │   └── lifespan.py            # async engine + sessionmaker
│   ├── entities/                  # DTO, енумы, Resolution, мапперы
│   ├── services/
│   │   ├── downloaders/           # контроллеры платформ (по одному на источник)
│   │   │   ├── base_source.py     # абстрактный BaseSourceController
│   │   │   ├── ydl_source.py      # yt-dlp специализация базы
│   │   │   ├── resolver.py        # URL → SourceEnum + Resolution
│   │   │   ├── schemes/           # pydantic-схемы для платформ (Kinovod)
│   │   │   └── *_source.py        # tiktok_api_source, vk_api_source, ...
│   │   ├── telegram/
│   │   │   ├── bot_controller.py  # обёртка aiogram, используется везде
│   │   │   ├── lifespan.py        # настройка webhook, FSM/Redis
│   │   │   ├── daily_report_service.py  # KPI-отчёт для /stats
│   │   │   └── web_app.py         # валидаторы initData Telegram Web App
│   │   ├── i18n/                  # gettext + кастомные Starlette / Taskiq middlewares
│   │   ├── redis/                 # connection pool dependency
│   │   └── сleanup/clear_old.py   # CleanupService (имя папки с кириллической 'с')
│   ├── task_manager/
│   │   ├── tasks.py               # save_video, get_video_info, process_inline_query, cleanup_old_files_task
│   │   ├── events.py              # хуки startup/shutdown воркера Taskiq (отдельный Bot)
│   │   ├── state.py               # SaverState / DatabaseState
│   │   └── dependencies.py        # сессия с rollback-on-IntegrityError
│   ├── telegram_bot/
│   │   ├── handlers/              # start, download, inline, stats, subscribe, exceptions
│   │   ├── middlewares/           # database, dao_provider, controller_provider, user
│   │   ├── filters/               # admin, source (URL → SourceEnum), subscribe
│   │   └── keyboards/             # inline + callbacks + videotheatre wizard
│   └── web/
│       ├── application.py         # фабрика FastAPI + Sentry init
│       ├── lifespan.py            # оркестрация старта DB / Redis / Taskiq / Bot
│       ├── api/monitoring/        # GET /api/health
│       └── webhook/               # POST /api/webhook/telegram/{token}
├── tests/                         # pytest
├── locales/                       # каталоги gettext (en, ru)
├── cookies/                       # cookies по источникам (gitignored)
├── downloads/                     # эфемерное хранилище для yt-dlp (gitignored)
├── deploy/docker-compose.dev.yml  # dev-only оверрайды (volumes, ports, --reload)
├── nginx-configs/                 # конфиги nginx + Cloudflare origin TLS
├── scripts/                       # entrypoints, компиляция локалей, утилиты
├── docker-compose.yml             # прод-база
├── Dockerfile                     # multi-stage (uv builder → python:3.14 prod + ffmpeg/aria2/Deno → dev)
├── Taskfile.yml                   # task-оркестрация
├── alembic.ini                    # конфиг миграций
├── pyproject.toml                 # PEP 621 проект + uv конфиг + конфиг тулзов
└── uv.lock                        # залоченный граф зависимостей
```

### Вспомогательные скрипты

| Скрипт | Назначение |
|---|---|
| `scripts/start_backend.sh` | Entrypoint API (компиляция `.po`, потом `python -m saver_backend` → gunicorn или uvicorn в зависимости от `RELOAD`) |
| `scripts/start_taskiq_worker.sh` | Entrypoint воркера, поддерживает `--with-reload` |
| `scripts/start_taskiq_scheduler.sh` | Entrypoint планировщика |
| `scripts/entrypoint_chrome.sh` | Опциональная обёртка над headless Chrome, заворачивающая весь egress в SOCKS5-прокси через redsocks + iptables |
| `scripts/patch_yt_dlp.py` | Патчит yt-dlp-экстрактор Yandex Music на использование HTTPS-URL |
| `scripts/create_instagram_session.py` | Создаёт `cookies/instagram_instaloader/<login>.session` из Netscape-экспорта cookies |
| `scripts/clear_old.py` | Самостоятельный CLI-скрипт очистки старых файлов в `downloads/` |
| `scripts/compile_po.sh` | `pybabel compile -d locales -D messages` (вызывается каждым entrypoint-скриптом контейнера) |

## 📝 Переменные окружения

Все переменные приложения с префиксом `SAVER_BACKEND_`. Compose-only переменные (используемые только в `docker-compose.yml`) — без префикса.

### Приложение

| Переменная | Тип | Дефолт | Описание |
|---|---|---|---|
| `SAVER_BACKEND_HOST` | str | `127.0.0.1` | Bind host |
| `SAVER_BACKEND_PORT` | int | `8000` | Bind port |
| `SAVER_BACKEND_WORKERS_COUNT` | int | `1` | Количество воркеров gunicorn |
| `SAVER_BACKEND_RELOAD` | bool | `False` | Если `True`, поднимаем uvicorn с reload (только dev) |
| `SAVER_BACKEND_ENVIRONMENT` | str | `local` | Свободный лейбл окружения (`local` / `dev` / `prod`) |
| `SAVER_BACKEND_LOG_LEVEL` | enum | `INFO` | Один из `NOTSET`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `FATAL` |

### База данных (PostgreSQL)

| Переменная | Тип | Дефолт | Описание |
|---|---|---|---|
| `SAVER_BACKEND_DB_HOST` | str | `localhost` | |
| `SAVER_BACKEND_DB_PORT` | int | `5432` | |
| `SAVER_BACKEND_DB_USER` | str | `saver_backend` | |
| `SAVER_BACKEND_DB_PASS` | str | `saver_backend` | |
| `SAVER_BACKEND_DB_BASE` | str | `saver_backend` | |
| `SAVER_BACKEND_DB_ECHO` | bool | `False` | Echo SQL-запросов (debug) |

### Redis

| Переменная | Тип | Дефолт | Описание |
|---|---|---|---|
| `SAVER_BACKEND_REDIS_HOST` | str | `saver_backend-redis` | |
| `SAVER_BACKEND_REDIS_PORT` | int | `6379` | |
| `SAVER_BACKEND_REDIS_USER` | str? | — | |
| `SAVER_BACKEND_REDIS_PASS` | str? | — | |
| `SAVER_BACKEND_REDIS_BASE` | int? | — | DB-индекс для FSM (Taskiq всегда использует DB 1) |

### Sentry (опционально)

| Переменная | Тип | Дефолт | Описание |
|---|---|---|---|
| `SAVER_BACKEND_SENTRY_DSN` | str? | — | Отключено, если пусто |
| `SAVER_BACKEND_SENTRY_SAMPLE_RATE` | float | `1.0` | |

### Telegram

| Переменная | Тип | Дефолт | Описание |
|---|---|---|---|
| `SAVER_BACKEND_TELEGRAM_BOT_TOKEN` | str | `42:TOKEN` | От [@BotFather](https://t.me/BotFather) |
| `SAVER_BACKEND_TELEGRAM_SECRET_TOKEN` | str | `verysecrettoken` | Сверяется с заголовком `X-Telegram-Bot-Api-Secret-Token` на каждый webhook |
| `SAVER_BACKEND_TELEGRAM_FILENAME_SUFIX` | str | ` [@saver]` | Суффикс к именам скачанных файлов (опечатка `SUFIX` сохранена в имени переменной) |
| `SAVER_BACKEND_SUBSCRIPTION_CHANNELS` | JSON list[str] | `["channel_username"]` | Каналы, на которые юзер должен быть подписан |
| `SAVER_BACKEND_ADMIN_CHAT_ID` | int | `-4816121008` | Чат, куда форвардятся неподдерживаемые URL |
| `SAVER_BACKEND_INSTAGRAM_ACCOUNT` | str | `username:password` | Одна строка `login:password` для источника Instaloader |
| `SAVER_BACKEND_TELEGRAM_BOT_API_URL` | str | `http://bot-api:8081` | URL self-hosted Bot API |

### Webhook

| Переменная | Тип | Дефолт | Описание |
|---|---|---|---|
| `SAVER_BACKEND_WEBHOOK_BASE_URL` | str | `http://saver_backend-api:8000/api/webhook` | На проде — публичный HTTPS-эндпоинт |
| `SAVER_BACKEND_WEBHOOK_TELEGRAM_PATH` | str | `/telegram` | Полный путь webhook'a — `<base>/telegram/<bot-token>` |

### VK / Yandex Music

| Переменная | Тип | Дефолт | Описание |
|---|---|---|---|
| `SAVER_BACKEND_VK_SERVICE_TOKEN` | JSON list[str] | `["vk_token"]` | Пул service-токенов VK; на каждый запрос выбирается один через `secrets.choice` |
| `SAVER_BACKEND_YM_TOKEN` | str | `ym_token` | Токен Yandex Music |

### Загрузчик

| Переменная | Тип | Дефолт | Описание |
|---|---|---|---|
| `SAVER_BACKEND_PROXIES` | JSON list[str] | `[]` | Общий пул прокси |
| `SAVER_BACKEND_PROXIES_RU` | JSON list[str] | `[]` | RU-прокси (для VK / Rutube / OK / Facebook / Kinovod и chrome-entrypoint) |
| `SAVER_BACKEND_DTO_EXPIRE_TIMEOUT` | int | `12` | Часов до протухания кэшированных DTO в FSM |

### Headless Chrome / межсервисные имена

| Переменная | Тип | Дефолт | Описание |
|---|---|---|---|
| `SAVER_BACKEND_TASKIQ_WORKER_HOST` | str | `saver_backend-taskiq-worker` | |
| `SAVER_BACKEND_CHROME_HOST` | str | `saver_backend-chrome` | Используется источником Kinovod через Playwright CDP |
| `SAVER_BACKEND_CHROME_PORT` | int | `9223` | Должен совпадать с флагом `--remote-debugging-port=` в compose-сервисе `chrome` |

### Compose-only (Python settings их не читает)

| Переменная | Дефолт | Описание |
|---|---|---|
| `SAVER_BACKEND_VERSION` | `latest` | Тег образа `saver_backend` |
| `NGINX_PORT` | `80` | Хост-порт, маппящийся на nginx :80 |
| `TELEGRAM_API_ID` | _обязательна_ | Telegram API ID для self-hosted Bot API ([my.telegram.org](https://my.telegram.org/apps)) |
| `TELEGRAM_API_HASH` | _обязательна_ | Telegram API hash для того же |
| `TELEGRAM_VERBOSITY` | `1` | Verbosity Bot API контейнера |

## 🔧 Качество кода

В проекте используются:

- **Ruff** — линтер и форматтер (конфиг в `pyproject.toml`)
- **MyPy** — статическая типизация
- **Pre-commit** — запускает всё перед каждым коммитом + хук `uv-lock` для синхронизации `uv.lock` (`.pre-commit-config.yaml`)

```bash
uv run ruff format saver_backend tests
uv run ruff check saver_backend tests --fix
uv run mypy saver_backend
```

CI прогоняет Ruff format / Ruff check / MyPy матрицей на каждый push.

## 🤝 Вклад

1. Форк + ветка от `dev`.
2. Изменения, тесты по необходимости.
3. Pre-commit хуки должны проходить (`uv run pre-commit run -a`).
4. PR в ветку `dev`.

## 📄 Лицензия

Проект распространяется под лицензией MIT — см. файл [LICENSE](LICENSE).

---

Сделано с помощью FastAPI, aiogram 3, Taskiq и yt-dlp.
