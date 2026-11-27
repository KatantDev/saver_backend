import asyncio
import logging
import time

from aiogram import Bot
from aiogram.types import User

from saver_backend.settings import settings


async def get_bot_user(bot: Bot, process: str) -> User:
    """
    Ask the Bot API server who the bot is, saying why when it does not answer.

    The api and the worker start with this request. When the server is up but
    cannot reach Telegram (prod 26.11: no outbound network for 18 minutes after
    a reboot), the request waits for the whole session timeout and the process
    exits; the log used to show only "Shutting down the broker".

    :param bot: The bot whose server is asked.
    :param process: Name of the process for the log, e.g. api or worker.
    :return: The bot user.
    """
    started = time.monotonic()
    try:
        return await bot.get_me()
    except (Exception, asyncio.CancelledError) as e:
        logging.error(
            "%s cannot start: the Bot API server %s did not answer getMe in %.0f s "
            "(%s: %s). It cannot reach Telegram or is still starting; "
            "docker restarts the %s.",
            process,
            settings.telegram_bot_api_url,
            time.monotonic() - started,
            type(e).__name__,
            e,
            process,
        )
        raise
