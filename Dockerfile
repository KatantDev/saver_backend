FROM python:3.11.4-slim-bullseye AS prod
RUN apt-get update && apt-get install -y \
  gcc \
  curl \
  ffmpeg \
  aria2 \
  && rm -rf /var/lib/apt/lists/*


RUN pip install poetry==1.8.2

# Configuring poetry
RUN poetry config virtualenvs.create false
RUN poetry config cache-dir /tmp/poetry_cache

# Copying requirements of a project
COPY pyproject.toml poetry.lock /app/src/
WORKDIR /app/src

# Installing requirements and removing gcc
RUN --mount=type=cache,target=/tmp/poetry_cache poetry install --only main \
  && apt-get purge -y gcc \
  && apt-get autoremove -y \
  && rm -rf /var/lib/apt/lists/*

# Copying actuall application
COPY . /app/src/
RUN --mount=type=cache,target=/tmp/poetry_cache poetry install --only main

CMD ["/usr/local/bin/python", "-m", "saver_backend"]

FROM prod AS dev

RUN --mount=type=cache,target=/tmp/poetry_cache poetry install
