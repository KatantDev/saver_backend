import asyncio
import logging
from typing import TYPE_CHECKING

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods.base import TelegramType

if TYPE_CHECKING:
    from aiogram.client.bot import Bot
    from aiogram.methods import Response, TelegramMethod


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
        Send the request, waiting and repeating it on TelegramRetryAfter.

        :param make_request: Next step of the request chain.
        :param bot: Bot that sends the request.
        :param method: Telegram method being sent.
        :return: Telegram response.
        """
        attempt = 1
        while True:
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as e:
                if attempt >= self.max_attempts or e.retry_after > self.max_wait:
                    raise
                logging.info(
                    "Telegram flood control on %s, retrying in %s s",
                    type(method).__name__,
                    e.retry_after,
                )
                await asyncio.sleep(e.retry_after)
                attempt += 1
