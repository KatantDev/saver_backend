import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution

if TYPE_CHECKING:
    from saver_backend.db.dao.video_cache_dao import VideoCacheDAO
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class BaseSourceController(ABC):
    """Asynchronous controller for downloading videos from different sources."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED

    def __init__(
        self,
        resolution: Resolution,
        telegram_bot_controller: "TelegramBotController",
        telegram_id: int,
        video_cache_dao: "VideoCacheDAO",
        message_id: int | None = None,
    ) -> None:
        self._resolution = resolution
        self._loop = asyncio.get_event_loop()
        self._video_cache_dao = video_cache_dao

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

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video information without downloading.

        Base implementation returns None as not all sources support this.

        :param url: URL of the video.
        :return: Dictionary with video information or None.
        """
        return None

    async def delete_processing_message(self) -> None:
        """Delete processing message."""
        if self._message_id:
            await self._telegram_bot_controller.delete_message(
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )

    async def send_video_from_cache(self, source_id: str) -> bool:
        """
        Send video from cache.

        :param source_id: Source ID of the video.
        :return: True if video was sent from cache, False otherwise.
        """
        cached_video = await self._video_cache_dao.get_by_source_id(
            source=self.SOURCE,
            source_id=source_id,
        )
        if not cached_video:
            return False

        logging.info(
            "Cache hit for source_id=%s. Sending by file_id.",
            source_id,
        )
        await self.delete_processing_message()
        await self._telegram_bot_controller.send_video_by_file_id(
            telegram_id=self._telegram_id,
            file_id=cached_video.file_id,
            url=self._resolution.url,
        )
        return True
