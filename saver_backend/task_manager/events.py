import logging

from aiogram.utils.i18n import I18n
from taskiq import TaskiqEvents, TaskiqState

from saver_backend.db.lifespan import init_db, shutdown_db
from saver_backend.services.telegram.bot_controller import TelegramBotController
from saver_backend.tkq import broker

logging.basicConfig(level=logging.INFO)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def startup(state: TaskiqState) -> None:
    """
    Startup worker.

    :param state: taskiq state.
    """
    init_db(state)

    i18n = I18n(path="locales", default_locale="en", domain="messages")
    state.i18n = i18n

    telegram_bot_controller = TelegramBotController(i18n=i18n)
    me = await telegram_bot_controller.bot.get_me()
    state.telegram_bot_controller = telegram_bot_controller
    logging.info(f"Bot {me.username} ({me.full_name}) started.")

    logging.info("Taskiq worker startup")


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def shutdown(state: TaskiqState) -> None:
    """
    Shutdown worker.

    :param state: taskiq state.
    """
    await state.telegram_bot_controller.close()
    await shutdown_db(state)

    logging.info("Taskiq worker shutdown")
