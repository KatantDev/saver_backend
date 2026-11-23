from contextlib import asynccontextmanager
from typing import AsyncGenerator

from aiogram.utils.i18n import I18n
from fastapi import FastAPI

from saver_backend.db.lifespan import init_db, shutdown_db
from saver_backend.services.error_journal import start_error_journal, stop_error_journal
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

    session_factory = init_db(app.state)
    init_redis(app)
    # The worker keeps its own journal; here only the API process is covered
    error_journal = None
    if not broker.is_worker_process:
        error_journal = start_error_journal("api", session_factory)

    if not broker.is_worker_process:
        await broker.startup()
        await init_telegram_bot_controller(app, i18n)
    app.middleware_stack = app.build_middleware_stack()

    yield
    if not broker.is_worker_process:
        await broker.shutdown()
        await shutdown_telegram_bot_controller(app)

    if error_journal is not None:
        await stop_error_journal(*error_journal, session_factory)
    await shutdown_db(app.state)
    await shutdown_redis(app)
