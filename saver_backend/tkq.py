from typing import Any

import taskiq_fastapi
from taskiq import AsyncBroker, AsyncResultBackend, InMemoryBroker
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from saver_backend.settings import settings

result_backend: AsyncResultBackend[Any] = RedisAsyncResultBackend(
    redis_url=str(settings.redis_url.with_path("/1")),
)
broker: AsyncBroker = ListQueueBroker(
    str(settings.redis_url.with_path("/1")),
).with_result_backend(result_backend)

if settings.environment.lower() == "pytest":
    broker = InMemoryBroker()

taskiq_fastapi.init(
    broker,
    "saver_backend.web.application:get_app",
)
