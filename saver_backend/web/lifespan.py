from contextlib import asynccontextmanager
from typing import AsyncGenerator

from aiogram.utils.i18n import I18n
from fastapi import FastAPI

from saver_backend.db.lifespan import init_db, shutdown_db
from saver_backend.services.i18n.starlette import I18nMiddleware
from saver_backend.services.redis.lifespan import init_redis, shutdown_redis
from saver_backend.services.telegram.lifespan import (
    init_telegram_bot_controller,
    shutdown_telegram_bot_controller,
)
from saver_backend.tkq import broker


@asynccontextmanager
async def lifespan_setup(
    app: FastAPI,
) -> AsyncGenerator[None, None]:  # pragma: no cover
    """
    Actions to run on application startup.

    This function uses fastAPI app to store data
    in the state, such as db_engine.

    :param app: the fastAPI application.
    :return: function that actually performs actions.
    """

    app.middleware_stack = None
    i18n = I18n(path="locales", default_locale="en", domain="messages")
    app.add_middleware(I18nMiddleware, i18n=i18n)

    if not broker.is_worker_process:
        await broker.startup()
    init_db(app.state)
    init_redis(app)
    await init_telegram_bot_controller(app, i18n)
    app.middleware_stack = app.build_middleware_stack()

    yield
    if not broker.is_worker_process:
        await broker.shutdown()
    await shutdown_db(app.state)
    await shutdown_redis(app)
    await shutdown_telegram_bot_controller(app)
