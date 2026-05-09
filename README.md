# Saver Backend

Telegram bot backend that downloads media from a wide range of social platforms and re-sends it directly inside Telegram chats. Built around FastAPI + aiogram 3 + Taskiq + yt-dlp, with a self-hosted Telegram Bot API server, a YouTube PO-token provider and a headless Chrome instance for sites that need a real browser.

[🇷🇺 Русская версия](README.ru.md)

## 📋 Table of Contents

- [Features](#-features)
- [Supported Platforms](#-supported-platforms)
- [Architecture](#-architecture)
- [Prerequisites](#-prerequisites)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [Development](#-development)
- [Deployment](#-deployment)
- [Project Structure](#-project-structure)
- [Environment Variables](#-environment-variables)
- [Code Quality](#-code-quality)
- [Contributing](#-contributing)
- [License](#-license)

## ✨ Features

- **18+ supported platforms** — videos, photo carousels, audio (full list below).
- **Webhook-only Telegram bot** with a self-hosted [aiogram/telegram-bot-api](https://github.com/aiogram/telegram-bot-api) server, allowing uploads up to 2 GB.
- **Background pipeline** powered by [Taskiq](https://taskiq-python.github.io/) on top of Redis, with progress messages and per-task time limits.
- **Two-process bot** — both the FastAPI app (webhook receiver) and the Taskiq worker run their own `Bot` instance and share Redis-backed FSM storage.
- **Quality / language picker** for long-form sources (YouTube, VK Video, Rutube, OK.ru, M3U8, Kinovod) with thumbnail preview.
- **Movie / series wizard** ("VideoTheatre") — multi-step keyboard for season → episode → translation/dub navigation.
- **Inline mode** — share TikTok and Instagram posts straight from any chat via `@botname <url>`. Empty inline query shows the user's last 20 cached short videos as a personal gallery.
- **Telegram `file_id` cache** in PostgreSQL — repeat downloads of the same video are instant; nothing is re-uploaded.
- **Subscription gate** — bot can require users to be members of a configurable list of channels before they can download.
- **i18n** in English and Russian, auto-selected from the user's Telegram client locale (aiogram `SimpleI18nMiddleware`).
- **Admin `/stats` command** — on-demand KPI report (new/active users, downloads, per-source breakdown).
- **Periodic cleanup** of old downloaded files (Taskiq cron, every 10 minutes).
- **Sentry** integration (optional) for error tracking, with FastAPI / SQLAlchemy / Redis / stdlib integrations pre-wired.
- **Forwards every unsupported URL** to a configurable admin chat for triage.

## 🌐 Supported Platforms

The resolver auto-detects the source by URL host and path. Each platform has its own controller in `saver_backend/services/downloaders/`.

| Platform | Backend | Auth required | Notes |
|---|---|---|---|
| YouTube (videos) | yt-dlp + aria2c + bgutil POT provider | cookies | quality picker |
| YouTube Shorts | yt-dlp + bgutil POT provider | cookies | direct download |
| Instagram (reels, posts, carousels) | indown.io scraper | none | primary handler for `/p/` and `/reel(s)/` |
| Instagram (stories) | [`instaloader`](https://instaloader.github.io/) | account session file | |
| TikTok | `tikwm.com` API | none | videos and photo slideshows |
| X (Twitter) | yt-dlp + `fixupx.com` fallback | none | |
| VK Video | yt-dlp via RU proxy | none | quality picker |
| VK Clips | yt-dlp via RU proxy | none | |
| VK wall posts & photos | [`vkbottle`](https://github.com/vkbottle/vkbottle) | VK service token(s) | mixed media (photo + video + audio) |
| Rutube | yt-dlp via RU proxy | none | quality picker |
| OK.ru | yt-dlp via RU proxy | none | quality picker |
| Pinterest | yt-dlp | none | |
| Dzen | yt-dlp | none | |
| Reddit | yt-dlp + aria2c | optional cookies | videos and animated GIFs |
| Facebook | yt-dlp via proxy | none | fb.com / m.fb.com / fb.watch |
| Kinovod | Playwright + headless Chrome over CDP + custom playerjs patch | none, but needs RU proxy | full films / series with season/episode wizard |
| Yandex Music | [`ymdantic`](https://pypi.org/project/ymdantic/) | YM token | tracks and full albums (audio) |
| M3U8 streams | yt-dlp | none | any URL ending in `.m3u8` |
| Adult sites | yt-dlp | optional cookies | hosts wired in `adult_ydl_source.py` |

## 🏗️ Architecture

The deployment is fully containerised. A single `docker-compose.yml` boots ten services:

| Service | Image | Role |
|---|---|---|
| `api` | built from `Dockerfile` | FastAPI app — receives Telegram webhook, exposes `/api/health` |
| `taskiq-worker` | same image | runs the download pipeline (5 GB / 3 CPU limit) |
| `taskiq-scheduler` | same image | cron-like scheduler (cleanup task) |
| `migrator` | same image | one-shot `alembic upgrade head` |
| `db` | `postgres:16.3-bullseye` | application database |
| `redis` | `valkey/valkey:8-alpine` | Taskiq broker (DB 1) + aiogram FSM storage. Valkey is the BSD-3 OSS fork of Redis (Linux Foundation), drop-in compatible |
| `nginx` | `nginx:1.28-trixie` | TLS termination, reverse proxy in front of `api` |
| `telegram-bot-api` | `aiogram/telegram-bot-api:9.6` | self-hosted Bot API (so the bot can send files up to 2 GB) |
| `bgutil` | `brainicism/bgutil-ytdlp-pot-provider:1.3.0` | yt-dlp PO-token provider for YouTube |
| `chrome` | `chromedp/headless-shell:148.0.7778.97` | headless Chrome over CDP, used by Kinovod source via Playwright |

High-level data flow:

```
Telegram → nginx (TLS) → api (FastAPI webhook)
                          │
                          ├─ aiogram dispatcher (FSM in Redis DB 0)
                          │
                          └─ Taskiq broker (Redis DB 1)
                                │
                                └─ taskiq-worker
                                     ├─ SourceResolver → platform controller (yt-dlp / indown / vkbottle / …)
                                     ├─ self-hosted telegram-bot-api → user (file uploaded once, file_id cached)
                                     └─ PostgreSQL (users, history, file_id cache)

taskiq-scheduler ── cron */10 * * * * ── cleanup_old_files_task → ./downloads
```

The bot is **webhook-only** — there is no polling fallback. Telegram must be able to reach `https://<your-domain>/api/webhook/telegram/<bot-token>`.

## 📦 Prerequisites

- **Python 3.14** (`pyproject.toml` requires `>=3.14,<4.0`)
- [`uv`](https://github.com/astral-sh/uv) `0.11+` for dependency management
- [Task](https://taskfile.dev/) for the deployment / dev shortcuts
- Docker Engine + Docker Compose v2
- For zero-downtime production deploys: [`docker rollout`](https://github.com/Wowu/docker-rollout) plugin on the deploy host
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Telegram **API ID and API hash** from [my.telegram.org](https://my.telegram.org/apps) — required by the self-hosted Bot API container
- A publicly reachable HTTPS domain that points to your nginx (Telegram refuses HTTP webhooks)
- Optional: a Sentry DSN, VK service token, Yandex Music token, Instagram account, RU SOCKS proxy

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/<your-account>/saver_backend.git
cd saver_backend

# 2. Install deps and pre-commit hooks
uv sync --all-groups
uv run pre-commit install

# 3. Configure
cp .env.example .env
$EDITOR .env   # fill in tokens, see "Environment Variables" below

# 4. Bring up the local stack
task build-local
task deploy-local
```

The local override (`deploy/docker-compose.dev.yml`) exposes Postgres on `5432`, Redis on `6379`, the API on `8000`, the self-hosted Bot API on `8081/8082` and bgutil on `4416`, plus mounts the source tree with `--reload` for `api` and both Taskiq processes.

To register the Telegram webhook, the bot needs to be reachable from Telegram's servers. Locally that usually means putting a tunnel (e.g. [`ngrok`](https://ngrok.com/), [`cloudflared`](https://github.com/cloudflare/cloudflared)) in front of the API and setting `SAVER_BACKEND_WEBHOOK_BASE_URL` accordingly.

## ⚙️ Configuration

### `.env` file

All application settings are loaded from `.env` via `pydantic-settings` and prefixed with `SAVER_BACKEND_`. See [Environment Variables](#-environment-variables) for the full reference. The Telegram Bot API container also reads two un-prefixed variables, `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`.

### Host UID matching

The prod Docker stage runs as a non-root user with UID/GID `1000` by default. For bind-mounted `./cookies` and `./downloads` to be writable from inside the container without host-side `chown`, the host owner of those directories must match. If your deploy user has a different UID/GID, set `APP_UID` / `APP_GID` in `.env`:

```bash
echo "APP_UID=$(id -u)" >> .env
echo "APP_GID=$(id -g)" >> .env
```

The next `task deploy` will rebuild the image with those IDs baked into the `app` user. For freshly created hosts where the directories don't exist yet, just `mkdir -p downloads cookies` as the deploy user.

### Cookies

Several sources need cookies for unauthenticated platforms or to bypass anti-bot checks. Drop Netscape-format `cookies*.txt` files into the matching subfolder of `cookies/`:

| Folder | Used by | Required? |
|---|---|---|
| `cookies/youtube_video_ydl/` | YouTube videos | yes |
| `cookies/youtube_shorts_ydl/` | YouTube Shorts | yes |
| `cookies/vk_api_ydl/` | VK API source (wall + photos) | optional |
| `cookies/adult_ydl/` | Adult source | optional |
| `cookies/instagram_instaloader/` | Instagram stories (`<login>.session` file) | yes |

The whole `cookies/*` tree is gitignored. The yt-dlp loader picks one file at random per request when several are present, so you can rotate cookies by adding more files to the folder.

### Instagram session

For Instagram stories, the `instaloader` source needs a saved session file:

1. Set `SAVER_BACKEND_INSTAGRAM_ACCOUNT="login:password"` in `.env`.
2. Place a Netscape cookies export of the same Instagram account at `cookies/instagram_instaloader/<login>.txt`.
3. Run `uv run python scripts/create_instagram_session.py` — this validates the cookies, calls `loader.test_login()` and writes `cookies/instagram_instaloader/<login>.session`.

### Database migrations

```bash
task migrate                    # via docker compose
uv run alembic upgrade head     # manually
```

There are 5 migrations under `saver_backend/db/migrations/versions/`. The `migrator` compose service runs `alembic upgrade head` once and exits.

## 💻 Development

### Task commands

The project uses [Taskfile](https://taskfile.dev/) for dev/deploy shortcuts. The full list:

| Command | Description |
|---|---|
| `task build-local` | Build images using `docker-compose.yml` + `deploy/docker-compose.dev.yml` |
| `task deploy-local` | Bring the dev stack up with auto-reload |
| `task migrate` | Build + run the `migrator` service |
| `task locales` | Extract → update → compile `.po` files (use `DEBUG=1 task locales` to keep source locations) |
| `task deploy` | Production deploy (zero-downtime via `docker rollout` if already running) |

### Localization

Translations live in `locales/{en,ru}/LC_MESSAGES/messages.po` and are compiled to `messages.mo` at container start (every entrypoint script runs `compile_po.sh`). Adding/updating strings:

```bash
task locales
```

This calls `pybabel extract` → `pybabel update` → `pybabel compile`. The project also has a Tolgee push workflow (`.github/workflows/tolgee_push.yml`) that syncs new keys to Tolgee on every push to `dev`/`main`.

### Tests

```bash
uv run pytest -vv
# or, against the dockerised stack:
docker compose run --rm api pytest -vv
```

CI runs `pytest` inside the built `dev` Docker target (`.github/workflows/tests.yml`).

## 🚢 Deployment

### Production deploy via Taskfile

```bash
task deploy
```

The task picks one of two strategies:

- **Cold start** — if `api` and `taskiq-worker` are not running, it builds and `up --build -d` everything.
- **Rolling update** — otherwise, it rebuilds, runs `docker rollout api`, runs `migrator`, reloads nginx, then `docker rollout taskiq-worker`. Requires the [`docker rollout`](https://github.com/Wowu/docker-rollout) plugin on the host.

### GitHub Actions

Three workflows live under `.github/workflows/`:

| Workflow | Triggers | What it does |
|---|---|---|
| `tests.yml` | every push | Lint matrix (black / ruff / mypy via pre-commit) + dockerised `pytest` |
| `deploy.yml` | successful `tests.yml` on `dev`/`main`, or manual dispatch | SSHes into the server, fetches the exact commit via a GitHub App token, writes `.env` from `secrets.ENV_FILE`, runs `task deploy` |
| `tolgee_push.yml` | push to `main`/`dev`, or manual | Pushes new translation keys to Tolgee |

Required GitHub configuration:

- **Repository variable**: `APP_ID` (GitHub App ID used to mint deploy tokens)
- **Environment secrets** (per `dev` and `prod` environment):
  - `APP_PRIVATE_KEY` — GitHub App private key
  - `SSH_HOST`, `SSH_USER`, `SSH_KEY`, `SSH_PORT`
  - `ENV_FILE` — full contents of the `.env` written to the server on each deploy
- **Tolgee secrets** (repo-level): `TOLGEE_API_URL`, `TOLGEE_API_KEY`, `TOLGEE_PROJECT_ID`

## 📁 Project Structure

```
saver_backend/
├── saver_backend/                 # Main application package
│   ├── __main__.py                # entrypoint (uvicorn reload OR gunicorn)
│   ├── gunicorn_runner.py         # custom UvicornWorker for gunicorn
│   ├── settings.py                # all SAVER_BACKEND_* env vars
│   ├── log.py                     # loguru config
│   ├── tkq.py                     # Taskiq broker, scheduler, FastAPI integration
│   ├── db/                        # SQLAlchemy 2.0 layer
│   │   ├── models/                # users, history, cache (file_id store)
│   │   ├── dao/                   # user / history / cache DAOs
│   │   ├── migrations/            # alembic
│   │   └── lifespan.py            # async engine + sessionmaker
│   ├── entities/                  # DTOs, enums, Resolution, mappers
│   ├── services/
│   │   ├── downloaders/           # platform controllers (one per source)
│   │   │   ├── base_source.py     # abstract BaseSourceController
│   │   │   ├── ydl_source.py      # yt-dlp specialisation of the base
│   │   │   ├── resolver.py        # URL → SourceEnum + Resolution
│   │   │   ├── schemes/           # platform-specific pydantic schemas (Kinovod)
│   │   │   └── *_source.py        # e.g. tiktok_api_source, vk_api_source, ...
│   │   ├── telegram/
│   │   │   ├── bot_controller.py  # aiogram wrapper used everywhere
│   │   │   ├── lifespan.py        # webhook setup, FSM/Redis wiring
│   │   │   ├── daily_report_service.py  # /stats KPI report
│   │   │   └── web_app.py         # Telegram Web App initData validators
│   │   ├── i18n/                  # gettext + custom Starlette / Taskiq middlewares
│   │   ├── redis/                 # connection pool dependency
│   │   └── cleanup/clear_old.py   # CleanupService
│   ├── task_manager/
│   │   ├── tasks.py               # save_video, get_video_info, process_inline_query, cleanup_old_files_task
│   │   ├── events.py              # Taskiq worker startup/shutdown hooks (separate Bot instance)
│   │   ├── state.py               # SaverState / DatabaseState
│   │   └── dependencies.py        # session provider with rollback-on-IntegrityError
│   ├── telegram_bot/
│   │   ├── handlers/              # start, download, inline, stats, subscribe, exceptions
│   │   ├── middlewares/           # database, dao_provider, controller_provider, user
│   │   ├── filters/               # admin, source (URL → SourceEnum), subscribe
│   │   └── keyboards/             # inline + callbacks + videotheatre wizard
│   └── web/
│       ├── application.py         # FastAPI factory + Sentry init
│       ├── lifespan.py            # DB / Redis / Taskiq / Bot startup orchestration
│       ├── api/monitoring/        # GET /api/health
│       └── webhook/               # POST /api/webhook/telegram/{token}
├── tests/                         # pytest
├── locales/                       # gettext catalogues (en, ru)
├── cookies/                       # per-source cookie folders (gitignored)
├── downloads/                     # ephemeral storage for yt-dlp output (gitignored)
├── deploy/docker-compose.dev.yml  # dev-only overrides (volumes, ports, --reload)
├── nginx-configs/                 # nginx + Cloudflare origin TLS configs
├── scripts/                       # entrypoints, locale compilers, helpers
├── docker-compose.yml             # production base
├── Dockerfile                     # multi-stage (uv builder → python:3.14 prod + ffmpeg/aria2/Deno → dev)
├── Taskfile.yml                   # task orchestration
├── alembic.ini                    # migration config
├── pyproject.toml                 # PEP 621 project + uv config + tool config
└── uv.lock                        # locked dependency graph
```

### Helper scripts

| Script | Purpose |
|---|---|
| `scripts/start_backend.sh` | API entrypoint (compiles `.po`, then `python -m saver_backend` → gunicorn or uvicorn depending on `RELOAD`) |
| `scripts/start_taskiq_worker.sh` | Worker entrypoint, supports `--with-reload` |
| `scripts/start_taskiq_scheduler.sh` | Scheduler entrypoint |
| `scripts/entrypoint_chrome.sh` | Optional headless-Chrome wrapper that routes all egress through a SOCKS5 proxy via redsocks + iptables |
| `scripts/patch_yt_dlp.py` | Monkey-patches `yt-dlp` Yandex Music extractor to use HTTPS URLs |
| `scripts/create_instagram_session.py` | Creates `cookies/instagram_instaloader/<login>.session` from a Netscape cookies export |
| `scripts/clear_old.py` | Standalone CLI cleanup of old files in `downloads/` |
| `scripts/compile_po.sh` | `pybabel compile -d locales -D messages` (run by every container entrypoint) |

## 📝 Environment Variables

All application variables are prefixed with `SAVER_BACKEND_`. Compose-only variables (used inside `docker-compose.yml`) are not.

### Application

| Variable | Type | Default | Description |
|---|---|---|---|
| `SAVER_BACKEND_HOST` | str | `127.0.0.1` | Bind host |
| `SAVER_BACKEND_PORT` | int | `8000` | Bind port |
| `SAVER_BACKEND_WORKERS_COUNT` | int | `1` | Gunicorn worker count |
| `SAVER_BACKEND_RELOAD` | bool | `False` | If `True`, run via uvicorn with reload (dev only) |
| `SAVER_BACKEND_ENVIRONMENT` | str | `local` | Free-form environment label (`local` / `dev` / `prod`) |
| `SAVER_BACKEND_LOG_LEVEL` | enum | `INFO` | One of `NOTSET`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `FATAL` |

### Database (PostgreSQL)

| Variable | Type | Default | Description |
|---|---|---|---|
| `SAVER_BACKEND_DB_HOST` | str | `localhost` | |
| `SAVER_BACKEND_DB_PORT` | int | `5432` | |
| `SAVER_BACKEND_DB_USER` | str | `saver_backend` | |
| `SAVER_BACKEND_DB_PASS` | str | `saver_backend` | |
| `SAVER_BACKEND_DB_BASE` | str | `saver_backend` | |
| `SAVER_BACKEND_DB_ECHO` | bool | `False` | Echo SQL queries (debug) |

### Redis

| Variable | Type | Default | Description |
|---|---|---|---|
| `SAVER_BACKEND_REDIS_HOST` | str | `saver_backend-redis` | |
| `SAVER_BACKEND_REDIS_PORT` | int | `6379` | |
| `SAVER_BACKEND_REDIS_USER` | str? | — | |
| `SAVER_BACKEND_REDIS_PASS` | str? | — | |
| `SAVER_BACKEND_REDIS_BASE` | int? | — | DB index for FSM (Taskiq always uses DB 1) |

### Sentry (optional)

| Variable | Type | Default | Description |
|---|---|---|---|
| `SAVER_BACKEND_SENTRY_DSN` | str? | — | Disabled when empty |
| `SAVER_BACKEND_SENTRY_SAMPLE_RATE` | float | `1.0` | |

### Telegram

| Variable | Type | Default | Description |
|---|---|---|---|
| `SAVER_BACKEND_TELEGRAM_BOT_TOKEN` | str | `42:TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `SAVER_BACKEND_TELEGRAM_SECRET_TOKEN` | str | `verysecrettoken` | Validated against the `X-Telegram-Bot-Api-Secret-Token` header on every incoming webhook |
| `SAVER_BACKEND_TELEGRAM_FILENAME_SUFIX` | str | ` [@saver]` | Suffix appended to downloaded filenames (note the typo `SUFIX` is preserved in the env var name) |
| `SAVER_BACKEND_SUBSCRIPTION_CHANNELS` | JSON list[str] | `["channel_username"]` | Channels users must be subscribed to before downloading |
| `SAVER_BACKEND_ADMIN_CHAT_ID` | int | `-4816121008` | Chat that receives unsupported-URL forwards |
| `SAVER_BACKEND_INSTAGRAM_ACCOUNT` | str | `username:password` | Single `login:password` string used by the Instaloader source |
| `SAVER_BACKEND_TELEGRAM_BOT_API_URL` | str | `http://bot-api:8081` | Self-hosted Bot API server URL |

### Webhook

| Variable | Type | Default | Description |
|---|---|---|---|
| `SAVER_BACKEND_WEBHOOK_BASE_URL` | str | `http://saver_backend-api:8000/api/webhook` | Set to your public HTTPS endpoint in production |
| `SAVER_BACKEND_WEBHOOK_TELEGRAM_PATH` | str | `/telegram` | Final webhook path is `<base>/telegram/<bot-token>` |

### VK / Yandex Music

| Variable | Type | Default | Description |
|---|---|---|---|
| `SAVER_BACKEND_VK_SERVICE_TOKEN` | JSON list[str] | `["vk_token"]` | Pool of VK service tokens; one is picked per request via `secrets.choice` |
| `SAVER_BACKEND_YM_TOKEN` | str | `ym_token` | Yandex Music access token |

### Downloader

| Variable | Type | Default | Description |
|---|---|---|---|
| `SAVER_BACKEND_PROXIES` | JSON list[str] | `[]` | General proxy pool |
| `SAVER_BACKEND_PROXIES_RU` | JSON list[str] | `[]` | RU-region proxies (used by VK / Rutube / OK / Facebook / Kinovod and the chrome entrypoint) |
| `SAVER_BACKEND_DTO_EXPIRE_TIMEOUT` | int | `12` | Hours before cached video DTOs expire from FSM state |

### Headless Chrome / inter-service hostnames

| Variable | Type | Default | Description |
|---|---|---|---|
| `SAVER_BACKEND_TASKIQ_WORKER_HOST` | str | `saver_backend-taskiq-worker` | |
| `SAVER_BACKEND_CHROME_HOST` | str | `saver_backend-chrome` | Used by the Kinovod source via Playwright CDP |
| `SAVER_BACKEND_CHROME_PORT` | int | `9223` | Must match the `--remote-debugging-port=` flag in the compose `chrome` service |

### Compose-only (not read by the Python settings)

| Variable | Default | Description |
|---|---|---|
| `SAVER_BACKEND_VERSION` | `latest` | Image tag for the `saver_backend` image |
| `NGINX_PORT` | `80` | Host port mapped to nginx :80 |
| `TELEGRAM_API_ID` | _required_ | Telegram API ID for the self-hosted Bot API container ([my.telegram.org](https://my.telegram.org/apps)) |
| `TELEGRAM_API_HASH` | _required_ | Telegram API hash for the same |
| `TELEGRAM_VERBOSITY` | `1` | Bot API container verbosity |

## 🔧 Code Quality

The project uses:

- **Ruff** — linter and formatter (configured in `pyproject.toml`)
- **MyPy** — static type checking
- **Pre-commit** — runs everything before each commit, plus `uv-lock` to keep `uv.lock` in sync (`.pre-commit-config.yaml`)

```bash
uv run ruff format saver_backend tests
uv run ruff check saver_backend tests --fix
uv run mypy saver_backend
```

CI runs Ruff format / Ruff check / MyPy as a matrix on every push.

## 🤝 Contributing

1. Fork the repo and create a feature branch.
2. Make your changes; add tests where reasonable.
3. Make sure pre-commit hooks pass (`uv run pre-commit run -a`).
4. Open a pull request against `dev`.

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

Built with FastAPI, aiogram 3, Taskiq and yt-dlp.
