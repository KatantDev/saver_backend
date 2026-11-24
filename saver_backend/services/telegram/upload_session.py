import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.methods.base import TelegramType
from aiogram.types import FSInputFile

if TYPE_CHECKING:
    from aiogram.client.bot import Bot
    from aiogram.methods import TelegramMethod

# Seconds the Bot API server gets per megabyte of our files. The request ends
# only when the server has passed the whole file on to Telegram, so a fixed
# 180 s cut off big uploads that were still going (10 on prod on 21-23.11).
SECONDS_PER_MEGABYTE = 1.0
UPLOAD_OVERHEAD_SECONDS = 120
MEGABYTE = 1024 * 1024


def _local_files(value: Any) -> list[FSInputFile]:
    """
    Files from the disk inside a request field.

    :param value: Field value of a Telegram method.
    :return: Files found in it, also inside media groups.
    """
    if isinstance(value, FSInputFile):
        return [value]
    if isinstance(value, (list, tuple)):
        return [file for item in value for file in _local_files(item)]
    media = getattr(value, "media", None)
    if media is not None and media is not value:
        return _local_files(media)
    return []


def upload_size(method: Any) -> int:
    """
    Bytes of the local files a request uploads.

    :param method: Telegram method.
    :return: Total size, 0 when the request carries no local files.
    """
    total = 0
    for name in type(method).model_fields:
        for file in _local_files(getattr(method, name, None)):
            try:
                total += Path(file.path).stat().st_size
            except OSError:
                continue
    return total


def upload_timeout(method: Any, base_timeout: float) -> float:
    """
    Time limit for a request, longer for big uploads.

    :param method: Telegram method.
    :param base_timeout: Limit for requests without files.
    :return: Limit in seconds, never below the base one.
    """
    size = upload_size(method)
    if not size:
        return base_timeout
    by_size = UPLOAD_OVERHEAD_SECONDS + size / MEGABYTE * SECONDS_PER_MEGABYTE
    return max(base_timeout, by_size)


class UploadSession(AiohttpSession):
    """Session that gives uploads of big files the time they need."""

    async def make_request(
        self,
        bot: "Bot",
        method: "TelegramMethod[TelegramType]",
        timeout: int | None = None,
    ) -> TelegramType:
        """
        Send the request with a time limit that fits the uploaded files.

        :param bot: Bot that sends the request.
        :param method: Telegram method being sent.
        :param timeout: Limit set by the caller, kept as is.
        :return: Result of the method.
        """
        if timeout is None:
            limit = upload_timeout(method, self.timeout)
            if limit > self.timeout:
                logging.info(
                    "Upload of %s gets %d s",
                    type(method).__name__,
                    limit,
                )
                timeout = int(limit)
        return await super().make_request(bot, method, timeout=timeout)
