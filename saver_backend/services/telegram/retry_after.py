import asyncio
import logging
import re
from typing import TYPE_CHECKING

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods.base import TelegramType

if TYPE_CHECKING:
    from aiogram.client.bot import Bot
    from aiogram.methods import Response, TelegramMethod
# The local Bot API server reports the flood pause of an upload as a plain
# "400 Bad Request: too Many Requests: retry after 8", without the retry_after
# parameter, so aiogram raises TelegramBadRequest instead of TelegramRetryAfter
FLOOD_IN_BAD_REQUEST_RE = re.compile(
    r"too many requests: retry after (\d+)",
    re.IGNORECASE,
)


def flood_pause(error: Exception) -> float | None:
    """
    Pause Telegram asked for, in seconds.

    :param error: Error raised by a Telegram request.
    :return: Seconds to wait, None when the error is not flood control.
    """
    if isinstance(error, TelegramRetryAfter):
        return float(error.retry_after)
    if isinstance(error, TelegramBadRequest):
        found = FLOOD_IN_BAD_REQUEST_RE.search(error.message or "")
        if found:
            return float(found.group(1))
    return None


class RetryAfterMiddleware(BaseRequestMiddleware):
    """Repeat a request after Telegram's flood-control pause."""

    def __init__(self, max_attempts: int = 3, max_wait: float = 30) -> None:
        """
        Set retry limits.

        :param max_attempts: How many times to send the request in total.
        :param max_wait: Longest pause in seconds worth waiting for; with a
            longer one the error is raised at once.
        """
        self.max_attempts = max_attempts
        self.max_wait = max_wait

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: "Bot",
        method: "TelegramMethod[TelegramType]",
    ) -> "Response[TelegramType]":
        """
        Send the request, waiting and repeating it on flood control.

        :param make_request: Next step of the request chain.
        :param bot: Bot that sends the request.
        :param method: Telegram method being sent.
        :return: Telegram response.
        """
        attempt = 1
        while True:
            try:
                return await make_request(bot, method)
            except (TelegramRetryAfter, TelegramBadRequest) as e:
                pause = flood_pause(e)
                if pause is None:
                    raise
                if attempt >= self.max_attempts or pause > self.max_wait:
                    raise
                logging.info(
                    "Telegram flood control on %s, retrying in %s s",
                    type(method).__name__,
                    pause,
                )
                await asyncio.sleep(pause)
                attempt += 1
