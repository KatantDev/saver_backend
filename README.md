# Saver Backend

A Telegram bot backend for downloading media content from various social media platforms.

[🇷🇺 Русская версия](README.ru.md)

## 📋 Table of Contents

- [Features](#features)
- [Supported Platforms](#supported-platforms)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Development](#development)
- [Deployment](#deployment)
- [Project Structure](#project-structure)
- [Code Quality](#-code-quality)
- [Environment Variables](#-environment-variables)
- [Contributing](#-contributing)
- [License](#-license)

## ✨ Features

- Download videos, photos, and audio from multiple social media platforms
- Telegram bot interface for easy interaction
- Background task processing with Taskiq
- Redis caching for improved performance
- PostgreSQL database for user history and data persistence
- Multi-language support (English, Russian)
- Resolution selection support
- Cookie-based authentication for platform access

## 🌐 Supported Platforms

- YouTube (Videos & Shorts)
- Instagram (API & yt-dlp)
- TikTok (API & yt-dlp)
- X (Twitter)
- VK (Videos & Clips)
- Rutube
- Pinterest
- Dzen
- M3U8 streams

## 📦 Prerequisites

- Python 3.10+
- [Poetry](https://python-poetry.org/) for dependency management
- [Task](https://taskfile.dev/) for task automation
- Docker and Docker Compose for deployment
- PostgreSQL 15+
- Redis 7+

## 🚀 Installation

1. **Clone the repository:**

```bash
git clone <repository-url>
cd saver_backend
```

2. **Install dependencies:**

```bash
poetry install
```

3. **Install pre-commit hooks:**

```bash
poetry run pre-commit install
```

Pre-commit hooks will automatically run before each commit and check:
- Code formatting with **Black**
- Code quality with **Ruff**
- Type checking with **MyPy**

4. **Set up environment variables:**

Copy the example environment file and configure it:

```bash
cp .env.example .env
```

Then edit `.env` with your actual values. See [Environment Variables](#-environment-variables) section for detailed descriptions.

## ⚙️ Configuration

### Cookies Setup

Some platforms require cookies for authentication. Place cookie files in the `cookies/` directory:

- `cookies/instagram_ydl/` - Instagram cookies
- `cookies/vk_clips_ydl/` - VK Clips cookies
- `cookies/vk_video_ydl/` - VK Video cookies
- `cookies/youtube_shorts_ydl/` - YouTube Shorts cookies
- `cookies/youtube_video_ydl/` - YouTube Video cookies

### Database Migrations

Run database migrations:

```bash
task migrate
```

Or manually with alembic:

```bash
poetry run alembic upgrade head
```

## 💻 Development

### Running Locally

1. **Build the local environment (first time only):**

```bash
task build-local
```

2. **Start the local environment:**

```bash
task deploy-local
```

This will start all required services in Docker containers.

### Available Task Commands

The project uses [Taskfile](https://taskfile.dev/) for task automation. Available commands:

- `task deploy` - Build and deploy to production
- `task migrate` - Run database migrations
- `task build-local` - Build local development environment
- `task deploy-local` - Run local development environment
- `task locales` - Extract and compile translation files

View all available tasks:

```bash
task --list
```

### Localization

To work with translations:

```bash
task locales
```

This will:
1. Extract all translatable strings from the codebase
2. Update existing translation files
3. Compile `.po` files to `.mo` format

Translation files are located in the `locales/` directory.

## 🚢 Deployment

### Production Deployment

1. **Deploy to production:**

```bash
task deploy
```

This command will:
- Build Docker images
- Check if containers are already running
- If running: perform zero-downtime rollout using `docker rollout`
- If not running: start all services with `docker-compose up`
- Run database migrations
- Reload nginx configuration

### Docker Compose Services

The application consists of several services:
- **api** - FastAPI web application
- **taskiq-worker** - Background task processor
- **taskiq-scheduler** - Task scheduler
- **migrator** - Database migration runner
- **nginx** - Reverse proxy server
- **postgres** - PostgreSQL database
- **redis** - Redis cache

## 📁 Project Structure

```
saver_backend/
├── saver_backend/           # Main application package
│   ├── services/
│   │   ├── downloaders/     # Media download sources
│   │   │   ├── base_source.py       # Abstract base class for all controllers
│   │   │   ├── resolver.py          # Source detection and resolution logic
│   │   │   ├── youtube_video_ydl_source.py
│   │   │   ├── instagram_api_source.py
│   │   │   ├── tiktok_api_source.py
│   │   │   └── ...                  # Other platform controllers
│   │   ├── telegram/        # Telegram bot services
│   │   └── redis/           # Redis integration
│   ├── telegram_bot/        # Telegram bot entry point
│   │   ├── handlers/        # Message and command handlers
│   │   ├── middlewares/     # Bot middlewares
│   │   ├── keyboards/       # Telegram keyboards
│   │   └── filters/         # Custom filters
│   ├── task_manager/        # Background tasks
│   │   ├── tasks.py         # Task definitions
│   │   └── dependencies.py  # Task dependencies
│   ├── web/                 # FastAPI web application
│   │   ├── api/             # REST API endpoints
│   │   └── webhook/         # Telegram webhook handlers
│   ├── db/                  # Database layer
│   │   ├── models/          # SQLAlchemy models
│   │   ├── dao/             # Data Access Objects
│   │   └── migrations/      # Alembic migrations
│   └── entities/            # Domain entities and DTOs
├── tests/                   # Test suite
├── locales/                 # Translation files
├── cookies/                 # Platform authentication cookies
├── downloads/               # Downloaded media storage
├── scripts/                 # Utility scripts
├── Taskfile.yml            # Task automation configuration
├── docker-compose.yml      # Docker Compose configuration
└── pyproject.toml          # Poetry dependencies and project config
```

### Key Architecture Components

#### 1. Download Sources (`saver_backend/services/downloaders/`)

All download sources are implemented based on the `BaseSourceController` abstract class. Each platform has its own controller that extends this base class.

**Base Controller** (`base_source.py`):
- Provides common interface for all download sources
- Handles caching, history tracking, and user management
- Manages download lifecycle and error handling

**Resolver** (`resolver.py`):
- Detects the source platform from URLs
- Routes requests to appropriate controllers
- Handles URL pattern matching and validation

**Platform Controllers**:
- Each platform has a dedicated controller (e.g., `youtube_video_ydl_source.py`)
- Implements platform-specific download logic
- Handles platform authentication and API calls

#### 2. Telegram Bot (`telegram_bot/`)

Entry point for all Telegram interactions:
- **handlers/** - Process user messages, commands, and callbacks
- **middlewares/** - Request processing pipeline (auth, i18n, etc.)
- **keyboards/** - Interactive keyboards for user interface
- **filters/** - Custom message filters

#### 3. Task Manager (`task_manager/`)

Manages all background task processing:
- **tasks.py** - Defines asynchronous tasks for media downloads
- All interactions with download controllers happen here
- Handles task scheduling and execution
- Provides task dependency injection

#### 4. Web API (`web/`)

FastAPI application for webhook handling and REST API:
- **api/** - REST endpoints for external integrations
- **webhook/** - Telegram webhook receivers

## 🔧 Code Quality

The project uses several tools to maintain code quality:

- **Black** - Code formatting
- **Ruff** - Fast Python linter
- **MyPy** - Static type checking
- **Pre-commit** - Git hooks for automatic checks

These tools run automatically on every commit via pre-commit hooks.

To run checks manually:

```bash
# Format code
poetry run black saver_backend tests

# Lint code
poetry run ruff check saver_backend tests --fix

# Type check
poetry run mypy saver_backend
```

## 📝 Environment Variables

All environment variables are prefixed with `SAVER_BACKEND_`. A complete `.env.example` file is provided in the repository.

### Application Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `SAVER_BACKEND_HOST` | Application host address | `127.0.0.1` |
| `SAVER_BACKEND_PORT` | Application port | `8000` |
| `SAVER_BACKEND_WORKERS_COUNT` | Number of Uvicorn workers | `1` |
| `SAVER_BACKEND_RELOAD` | Enable hot reload (dev only) | `False` |
| `SAVER_BACKEND_ENVIRONMENT` | Environment name (local/dev/prod) | `local` |
| `SAVER_BACKEND_LOG_LEVEL` | Logging level | `INFO` |

### Database Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `SAVER_BACKEND_DB_HOST` | PostgreSQL host | `localhost` |
| `SAVER_BACKEND_DB_PORT` | PostgreSQL port | `5432` |
| `SAVER_BACKEND_DB_USER` | Database user | `saver_backend` |
| `SAVER_BACKEND_DB_PASS` | Database password | `saver_backend` |
| `SAVER_BACKEND_DB_BASE` | Database name | `saver_backend` |
| `SAVER_BACKEND_DB_ECHO` | Echo SQL queries (debug) | `False` |

### Redis Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `SAVER_BACKEND_REDIS_HOST` | Redis host | `saver_backend-redis` |
| `SAVER_BACKEND_REDIS_PORT` | Redis port | `6379` |
| `SAVER_BACKEND_REDIS_USER` | Redis username (optional) | - |
| `SAVER_BACKEND_REDIS_PASS` | Redis password (optional) | - |
| `SAVER_BACKEND_REDIS_BASE` | Redis database number (optional) | - |

### Sentry Configuration (Optional)

| Variable | Description | Default |
|----------|-------------|---------|
| `SAVER_BACKEND_SENTRY_DSN` | Sentry DSN for error tracking | - |
| `SAVER_BACKEND_SENTRY_SAMPLE_RATE` | Sample rate for error tracking | `1.0` |

### Telegram Bot Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `SAVER_BACKEND_TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather | **Required** |
| `SAVER_BACKEND_TELEGRAM_SECRET_TOKEN` | Secret token for webhook validation | `verysecrettoken` |
| `SAVER_BACKEND_SUBSCRIPTION_CHANNELS` | JSON array of required subscription channels | `["channel_username"]` |
| `SAVER_BACKEND_ADMIN_CHAT_ID` | Admin chat ID for notifications | **Required** |
| `SAVER_BACKEND_INSTAGRAM_ACCOUNTS` | JSON array of Instagram credentials | `["username:password"]` |
| `SAVER_BACKEND_TELEGRAM_BOT_API_URL` | Local Bot API server URL | `http://bot-api:8081` |

### Webhook Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `SAVER_BACKEND_WEBHOOK_BASE_URL` | Base URL for webhooks | `http://saver_backend-api:8000/api/webhook` |
| `SAVER_BACKEND_WEBHOOK_TELEGRAM_PATH` | Telegram webhook path | `/telegram` |

### Downloader Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `SAVER_BACKEND_PROXIES` | JSON array of proxy URLs | `[]` |

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Ensure all tests pass and pre-commit hooks succeed
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

Built with ❤️ using FastAPI, Aiogram, and yt-dlp
