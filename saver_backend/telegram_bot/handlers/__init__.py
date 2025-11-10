from aiogram import Dispatcher

from saver_backend.telegram_bot.handlers.download import download_router
from saver_backend.telegram_bot.handlers.exceptions import exception_router
from saver_backend.telegram_bot.handlers.inline import inline_router
from saver_backend.telegram_bot.handlers.start import start_router
from saver_backend.telegram_bot.handlers.stats import stats_router
from saver_backend.telegram_bot.handlers.subscribe import subscribe_router


def setup_handlers(dispatcher: Dispatcher) -> None:
    """
    Register all handlers in the dispatcher.

    :param dispatcher: The aiogram Dispatcher instance.
    """
    routers = [
        start_router,
        stats_router,
        subscribe_router,
        download_router,
        inline_router,
        exception_router,
    ]
    for router in routers:
        dispatcher.include_router(router)
