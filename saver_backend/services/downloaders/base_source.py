import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution

if TYPE_CHECKING:
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class BaseSourceController(ABC):
    """Asynchronous controller for downloading videos from different sources."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED

    def __init__(
        self,
        resolution: Resolution,
        telegram_bot_controller: "TelegramBotController",
        telegram_id: int,
        message_id: int | None = None,
    ) -> None:
        self._resolution = resolution
        self._loop = asyncio.get_event_loop()

        self._telegram_bot_controller = telegram_bot_controller
        self._telegram_id = telegram_id
        self._message_id = message_id
        self._last_percent = 0

    def _process_percent(self, percent: int) -> None:
        """
        Process message.

        :param percent: Percent of the video.
        """
        self._last_percent = percent

        if self._message_id is None:
            coro = self._telegram_bot_controller.send_start_downloading(
                telegram_id=self._telegram_id,
                percent=percent,
            )
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            try:
                message_id = future.result(timeout=5)
                if message_id:
                    self._message_id = message_id
            except TimeoutError:
                logging.warning(
                    "Timeout waiting for start message to be sent (url=%r)",
                    self._resolution.url,
                )
        else:
            coro = self._telegram_bot_controller.send_update_downloading(
                telegram_id=self._telegram_id,
                message_id=self._message_id,
                percent=percent,
            )
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    @abstractmethod
    async def download_video(self) -> None:
        """
        Download video.

        :return: Dictionary with video information.
        """
        raise NotImplementedError
