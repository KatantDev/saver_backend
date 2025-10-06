import logging

from aiogram.utils.i18n import I18n
from fastapi import FastAPI

from saver_backend.services.telegram.bot_controller import TelegramBotController


async def init_telegram_bot_controller(app: FastAPI, i18n: I18n) -> None:
    """
    Initialize telegram bot controller.

    :param app: Instance of application.
    :param i18n: I18n context.
    """
    telegram_bot_controller = TelegramBotController(i18n=i18n)
    me = await telegram_bot_controller.bot.get_me()
    logging.info(f"Bot {me.username} ({me.full_name}) started.")

    started = await telegram_bot_controller.startup()
    if started:
        logging.info("Telegram bot started successfully.")
        telegram_bot_controller.setup_middlewares(
            session_factory=app.state.db_session_factory,
        )

    app.state.telegram_bot_controller = telegram_bot_controller


async def shutdown_telegram_bot_controller(app: FastAPI) -> None:
    """
    Shutdown telegram bot controller.

    :param app: Instance of application.
    """
    await app.state.telegram_bot_controller.close()
