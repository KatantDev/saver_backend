import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from aiogram.types import Video
from sentry_sdk import capture_exception

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.entities.user import UserDTO
from saver_backend.services.downloaders.exceptions import UserInfoNotFoundError
from saver_backend.services.downloaders.schema import (
    VideoCacheDTO,
    VideoDTO,
)

if TYPE_CHECKING:
    from saver_backend.db.dao.user_dao import UserDAO
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
        user_dao: "UserDAO",
        message_id: int | None = None,
        format_id: str | None = None,
    ) -> None:
        self._resolution = resolution
        self._loop = asyncio.get_event_loop()
        self._video: VideoDTO | None = None
        self._video_cache_dao = video_cache_dao
        self._user_dao = user_dao
        self._user_info: UserDTO | None = None

        self._selected_format_id = format_id
        self._telegram_bot_controller = telegram_bot_controller
        self._telegram_id = telegram_id
        self._message_id = message_id
        self._last_percent = 0

    async def set_user_language(self, language: str | None = None) -> None:
        """
        Set user language.

        :param language: The language to set.
        """
        if language is None:
            user = await self._get_user_info()
            language = user.language

        self._telegram_bot_controller.language = language

    async def _get_user_info(self) -> UserDTO:
        """
        Get information about a user.

        :return: A UserDTO object.
        """
        if self._user_info is not None:
            return self._user_info

        model = await self._user_dao.get_by_id(telegram_id=self._telegram_id)
        if not model:
            raise UserInfoNotFoundError("User %s not found" % self._telegram_id)
        return UserDTO.from_db(model)

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

    async def _send_error_message(self, with_delete: bool = True) -> None:
        """
        Send error message.

        :param with_delete: If true, delete by message_id progress.
        """
        if self._message_id and with_delete:
            await self._telegram_bot_controller.delete_message(
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )
        await self._telegram_bot_controller.send_error_downloading(
            telegram_id=self._telegram_id,
        )

    async def delete_processing_message(self) -> None:
        """Delete processing message."""
        if self._message_id:
            await self._telegram_bot_controller.delete_message(
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )

    async def _send_video(
        self,
        video_dto: VideoDTO,
        supports_streaming: bool = True,
    ) -> None:
        """
        Sends the video to the user and then caches the result.

        :param supports_streaming: Flag to indicate if the video supports streaming.
        """
        telegram_video = await self._telegram_bot_controller.send_finish_downloading(
            video=video_dto,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
            supports_streaming=supports_streaming,
        )

        if telegram_video:
            await self._save_video_to_cache(video_dto, telegram_video)

    async def _save_video_to_cache(
        self,
        video_dto: VideoDTO,
        telegram_video: Video,
    ) -> None:
        """
        Save video details to the cache.

        :param telegram_video: The Video object from aiogram after sending.
        """
        if not video_dto.source_id or not video_dto.quality:
            logging.warning("Cannot cache video: source_id or quality is missing.")
            return

        dto_for_cache = video_dto.model_copy(
            update={"path": None, "thumbnail": None},
        )
        cache_dto = VideoCacheDTO.from_yt_dlp(
            source=self.SOURCE,
            telegram_video=telegram_video,
            video=dto_for_cache,
        )
        if not cache_dto:
            return

        try:
            await self._video_cache_dao.create(cache_dto)
            logging.info(
                "Successfully cached video with source_id=%s, quality=%s",
                cache_dto.source_id,
                cache_dto.quality,
            )
        except Exception as e:
            logging.error(
                "Failed to save video cache for source_id=%s: %s",
                video_dto.source_id,
                e,
                exc_info=True,
            )
            capture_exception(e)

    async def send_video_from_cache(self, source_id: str, quality: str) -> bool:
        """
        Send video from cache.

        :param source_id: Source ID of the video.
        :param quality: Quality of the video.
        :return: True if video was sent from cache, False otherwise.
        """
        cached_video = await self._video_cache_dao.get_by_source_id_and_quality(
            source=self.SOURCE,
            source_id=source_id,
            quality=quality,
        )
        if not cached_video:
            return False

        logging.info(
            "Cache hit for source_id=%s, quality=%s. Sending by file_id.",
            source_id,
            quality,
        )
        await self.delete_processing_message()
        await self._telegram_bot_controller.send_video_by_file_id(
            telegram_id=self._telegram_id,
            file_id=cached_video.file_id,
            url=self._resolution.url,
        )
        return True
