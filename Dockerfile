# -----------------------------------
# STAGE BUILDER: Prepare builder image
# Only install main dependencies
# -----------------------------------
FROM ghcr.io/astral-sh/uv:0.11.11-python3.14-trixie-slim@sha256:3e70f580d0e63d78408c35d332d780024b6e1d46d9744c888e22fa944393448e AS builder
RUN apt-get update && apt-get install -y \
  gcc \
  && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/.venv \
    VIRTUAL_ENV="/opt/.venv" \
    PATH="/opt/.venv/bin:$PATH" \
    UV_NO_DEV=1

WORKDIR /app/src

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# -----------------------------------
# STAGE PROD: Production image
# Copy dependencies and environment from builder image
# -----------------------------------
FROM python:3.14-slim-trixie@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033 AS prod

# Runtime dependencies for yt-dlp / ffmpeg-python / Yandex Music PoC.
# `--no-install-recommends` keeps the attack surface lean (Debian's recommends
# for ffmpeg pull GPU/HW-accel libraries we don't use in a headless worker).
RUN apt-get update && apt-get install -y --no-install-recommends \
  curl \
  ffmpeg \
  aria2 \
  && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. Worker processes attacker-controlled URLs through
# yt-dlp / ffmpeg / aria2; running as root would turn any subprocess RCE
# straight into container root. Default UID/GID 1000 matches typical
# host-user mappings — override at build time if your host uses a different
# UID for the bind-mounted ./cookies and ./downloads directories.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --system --gid ${APP_GID} app \
  && useradd  --system --uid ${APP_UID} --gid app --home /app --shell /usr/sbin/nologin app

# Deno is used by the bgutil-ytdlp-pot-provider plugin (YouTube PO-token)
COPY --from=denoland/deno:bin-2.7.1@sha256:61d9e9c5284d87b0efd2509905b130c77ba0d5224f30df10ab70c03e9174c208 /deno /usr/local/bin/deno

ENV VIRTUAL_ENV="/opt/.venv" \
    PATH="/opt/.venv/bin:$PATH"

COPY --from=builder --chown=app:app /app/src /app/src
COPY --from=builder --chown=app:app /opt/.venv /opt/.venv

WORKDIR /app/src
USER app

CMD ["python", "-m", "saver_backend"]

# -----------------------------------
# STAGE DEVELOPMENT: Development image
# Adds dev dependencies (pytest, mypy, ruff, etc.). Stays as root because
# bind-mounting the host repo over /app/src in deploy/docker-compose.dev.yml
# is incompatible with a fixed in-container UID across heterogeneous host
# filesystems. The dev stage is for local-dev / CI only — never deployed.
# -----------------------------------
FROM builder AS dev

# Runtime tools needed when the dev image is used as the API/worker container
# (deploy/docker-compose.dev.yml mounts source on top of /app/src and reuses
# this image for `api`/`taskiq-worker`, so it must be runtime-complete).
RUN apt-get update && apt-get install -y --no-install-recommends \
  curl \
  ffmpeg \
  aria2 \
  && rm -rf /var/lib/apt/lists/*

COPY --from=denoland/deno:bin-2.7.1@sha256:61d9e9c5284d87b0efd2509905b130c77ba0d5224f30df10ab70c03e9174c208 /deno /usr/local/bin/deno

ENV UV_NO_DEV=0

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-groups

CMD ["python", "-m", "saver_backend"]
