import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods.base import TelegramType

from saver_backend.services.telegram.upload_session import upload_size

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


# A file upload that hits flood control is refused only after the whole file
# has gone to the Bot API server, and with three attempts 9 finished downloads
# on prod on 25-26.11 ended with an error (one after 7 minutes of uploading).
# The pause is always 8 seconds, so more attempts cost little.
UPLOAD_ATTEMPTS = 6


# An upload that broke on the way and is worth one more try (prod 24-26.11:
# «Server disconnected», «internal Server Error during file upload»). A timeout
# is not here: the file may still arrive and would come twice.
DROPPED_UPLOAD_HINTS = (
    "server disconnected",
    "failed to send bytes",
    "connection reset",
    "internal server error during file upload",
)


def is_dropped_upload(error: Exception) -> bool:
    """Whether an upload broke on the way and can simply be sent again."""
    text = str(error).lower()
    return any(hint in text for hint in DROPPED_UPLOAD_HINTS)


class RetryAfterMiddleware(BaseRequestMiddleware):
    """
    Repeat a request after Telegram's flood-control pause.

    Flood control is counted per chat. A user who sends a batch of links gets
    several uploads and progress edits into one chat at once (prod 25.11: nine
    downloads in five minutes), every one of them is refused and repeated
    together with the others. So the uploads of one chat go one at a time and,
    while a chat is paused, the other requests to it wait for the pause first.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        max_wait: float = 30,
        upload_attempts: int = UPLOAD_ATTEMPTS,
    ) -> None:
        """
        Set retry limits.

        :param max_attempts: How many times to send the request in total.
        :param max_wait: Longest pause in seconds worth waiting for; with a
            longer one the error is raised at once.
        :param upload_attempts: The same limit for requests that upload files.
        """
        self.max_attempts = max_attempts
        self.max_wait = max_wait
        self.upload_attempts = upload_attempts
        # chat id -> monotonic time until which Telegram asked to wait
        self._paused_until: dict[int, float] = {}
        # chat id -> [lock, how many requests hold or wait for it]
        self._upload_locks: dict[int, tuple[asyncio.Lock, list[int]]] = {}

    def pause_left(self, chat_id: int | str | None) -> float:
        """
        Seconds left of the pause Telegram asked for in a chat.

        :param chat_id: Chat the request goes to.
        :return: Seconds to wait, 0 when the chat is not paused.
        """
        if not isinstance(chat_id, int):
            return 0.0
        until = self._paused_until.get(chat_id)
        if until is None:
            return 0.0
        left = until - time.monotonic()
        if left <= 0:
            del self._paused_until[chat_id]
            return 0.0
        return left

    @asynccontextmanager
    async def _one_upload_per_chat(self, chat_id: int) -> AsyncIterator[None]:
        lock, users = self._upload_locks.setdefault(chat_id, (asyncio.Lock(), [0]))
        users[0] += 1
        try:
            async with lock:
                yield
        finally:
            users[0] -= 1
            if not users[0]:
                self._upload_locks.pop(chat_id, None)

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
        chat_id = getattr(method, "chat_id", None)
        if isinstance(chat_id, int) and upload_size(method):
            async with self._one_upload_per_chat(chat_id):
                return await self._send(
                    make_request,
                    bot,
                    method,
                    self.upload_attempts,
                )
        return await self._send(make_request, bot, method, self.max_attempts)

    async def _send(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: "Bot",
        method: "TelegramMethod[TelegramType]",
        attempts: int,
    ) -> "Response[TelegramType]":
        chat_id = getattr(method, "chat_id", None)
        # Another request to this chat was just told to wait
        left = self.pause_left(chat_id)
        if left:
            await asyncio.sleep(left)
        attempt = 1
        resent = False
        while True:
            try:
                return await make_request(bot, method)
            except TelegramNetworkError as e:
                # The connection to the Bot API server dropped during an upload
                if resent or not upload_size(method) or not is_dropped_upload(e):
                    raise
                resent = True
                logging.warning(
                    "Upload of %s was dropped (%s), sending it once more",
                    type(method).__name__,
                    e,
                )
            except (TelegramRetryAfter, TelegramBadRequest) as e:
                if not resent and upload_size(method) and is_dropped_upload(e):
                    resent = True
                    logging.warning(
                        "Upload of %s failed on Telegram's side (%s), once more",
                        type(method).__name__,
                        e,
                    )
                    continue
                pause = flood_pause(e)
                if pause is None:
                    raise
                if attempt >= attempts or pause > self.max_wait:
                    raise
                logging.info(
                    "Telegram flood control on %s, retrying in %s s (attempt %s of %s)",
                    type(method).__name__,
                    pause,
                    attempt,
                    attempts,
                )
                if isinstance(chat_id, int):
                    self._paused_until[chat_id] = time.monotonic() + pause
                await asyncio.sleep(pause)
                attempt += 1
