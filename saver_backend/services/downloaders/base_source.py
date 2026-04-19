import asyncio
import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Sequence

from aiogram.types import Audio, Message, Video

from saver_backend.db.models.cache_model import CacheModel
from saver_backend.entities.enums import ContentTypeEnum, ProxyType, SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.entities.user import UserDTO
from saver_backend.services.downloaders.exceptions import UserInfoNotFoundError
from saver_backend.services.downloaders.schema import (
    AudioDTO,
    CacheDTO,
    PhotoDTO,
    PhotoListDTO,
    VideoDTO,
    VideoTheatreDTO,
)
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings

if TYPE_CHECKING:
    from saver_backend.db.dao.cache_dao import CacheDAO
    from saver_backend.db.dao.history_dao import HistoryDAO
    from saver_backend.db.dao.user_dao import UserDAO
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class BaseSourceController(ABC):
    """Asynchronous controller for downloading videos from different sources."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.LOCAL

    def __init__(
        self,
        resolution: Resolution,
        telegram_bot_controller: "TelegramBotController",
        telegram_id: int,
        user_dao: "UserDAO",
        history_dao: "HistoryDAO",
        cache_dao: "CacheDAO",
        message_id: int | None = None,
        format_id: str | None = None,
        inline_query_id: str | None = None,
    ) -> None:
        self._resolution = resolution
        self._loop = asyncio.get_event_loop()
        self._user_dao = user_dao
        self._history_dao = history_dao
        self._cache_dao = cache_dao
        self._user_info: UserDTO | None = None

        self._selected_format_id = format_id
        self._telegram_bot_controller = telegram_bot_controller
        self._telegram_id = telegram_id
        self._message_id = message_id
        self._inline_query_id = inline_query_id
        self._last_percent = 0

        # Proxies
        proxies = self._select_proxies()
        random.shuffle(proxies)
        self._proxy: str | None = None
        self._proxies: list[str] = []
        if proxies:
            self._proxy, *self._proxies = proxies

    @abstractmethod
    async def close(self) -> None:
        """Close resources if needed."""

    def _select_proxies(self) -> list[str]:
        """
        Selects a list of proxies based on the controller's PROXY_TYPE.

        - LOCAL: Uses the general proxy list.
        - RU: Uses the Russian proxy list, falling back to general if empty.
        - ALL: Uses a combination of both lists.

        :return: A list of selected proxy URLs.
        """
        local_proxies = settings.proxies
        ru_proxies = settings.proxies_ru

        if self.PROXY_TYPE == ProxyType.LOCAL:
            return local_proxies
        if self.PROXY_TYPE == ProxyType.RU:
            return ru_proxies or local_proxies  # Fallback to local
        if self.PROXY_TYPE == ProxyType.ALL:
            return local_proxies + ru_proxies
        return []

    async def set_user_language(self, language: str | None = None) -> None:
        """
        Set user language.

        :param language: The language to set.
        """
        if language is None:
            user = await self._get_user_info()
            language = user.language
        # A stub for other languages
        if language not in ("ru", "en"):
            language = "en"
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
        if self._inline_query_id:
            return

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
        Send error message, handling both direct messages and inline queries.

        :param with_delete: If true, delete by message_id progress.
        """
        if self._inline_query_id:
            await self._telegram_bot_controller.answer_inline_query_error(
                inline_query_id=self._inline_query_id,
            )
            return

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
            cache_model = await self._save_content_to_cache(video_dto, telegram_video)
            await self._create_history_entry(cache_model)

        if self._inline_query_id:
            await self._answer_inline_query(
                video_dto=video_dto,
                telegram_video=telegram_video,
            )

    async def _send_audio(
        self,
        audio_dto: AudioDTO,
    ) -> None:
        """Sends the audio to the user and then caches the result."""
        telegram_audio = (
            await (
                self._telegram_bot_controller.send_finish_downloading_audio(
                    audio=audio_dto,
                    telegram_id=self._telegram_id,
                    message_id=self._message_id,
                )
            )
        )

        if telegram_audio:
            audio_dto.media_url = None
            cache_model = await self._save_content_to_cache(audio_dto, telegram_audio)
            await self._create_history_entry(cache_model)

    async def _send_audio_group(
        self,
        audios: list[AudioDTO],
    ) -> None:
        """
        Send audio group and save to cache if not exists.

        :param audios:   Original AudioDTO list
        """
        if not audios:
            return

        tg_messages: list[Message] | None = (
            await self._telegram_bot_controller.send_finish_downloading_audio_group(
                files=audios,
                telegram_id=self._telegram_id,
                message_id=self._message_id,
                language=self._telegram_bot_controller.language,
            )
        )

        if not tg_messages:
            return

        for audio_dto, message in zip(audios, tg_messages):
            if not message.audio:
                continue
            audio_dto.media_url = None
            cache_model = await self._save_content_to_cache(
                audio_dto,
                message.audio,
            )

            if cache_model:
                await self._create_history_entry(cache_model)

    async def _save_content_to_cache(
        self,
        content_dto: VideoDTO | PhotoDTO | AudioDTO | PhotoListDTO,
        telegram_video: Video | Audio,
    ) -> CacheModel | None:
        """
        Save content details to the cache.

        If the URL contained a specific 'code' (e.g., short ID) and it differs
        from the actual source_id returned by the API, we save the cache entry TWICE:
        1. By the real source_id (primary)
        2. By the URL code (alias) for fast lookup next time.

        :param content_dto: The original DTO with metadata.
        :param telegram_video: The Video object from aiogram after sending.
        """
        dto_for_cache = content_dto.model_copy(update={"path": None, "thumbnail": None})
        cache_dto = CacheDTO.from_telegram_object(
            source=self.SOURCE,
            telegram_video=telegram_video,
            content_dto=dto_for_cache,
            quality=content_dto.quality,
        )
        if not cache_dto:
            return None

        model_to_return = await self._create_cache_entry_if_not_exists(cache_dto)

        url_code = self._resolution.metadata.get("code")
        if url_code and url_code != cache_dto.source_id:
            alias_cache_dto = cache_dto.model_copy(update={"source_id": url_code})
            alias_model = await self._create_cache_entry_if_not_exists(alias_cache_dto)
            if alias_model:
                model_to_return = alias_model

        return model_to_return

    async def create_or_update_cache_entry(
        self,
        content_dto: VideoTheatreDTO,
    ) -> CacheModel | None:
        """
        Create a new cache entry or update existing one if it already exists.

        Updates only meta_data field and updated_at timestamp.

        :param content_dto: The content DTO object.
        :return: The created or updated CacheModel instance,
         or None if operation failed.
        """
        dto_for_cache = content_dto.model_copy(update={"path": None, "thumbnail": None})
        cache_dto = CacheDTO.from_dto_object(
            source=self.SOURCE,
            content_dto=dto_for_cache,
        )
        if cache_dto is None:
            return None
        logging.info(
            "Successfully cached content with source_id=%s, quality=%s",
            cache_dto.source_id,
            cache_dto.quality,
        )

        return await self._cache_dao.update_or_create(cache_dto)

    async def get_dto_from_cache(
        self,
        source_id: str,
        timeout: int = 3,
    ) -> CacheModel | None:
        """Returns dto object from cache or None if not found or expired."""
        cached_dto = await self._cache_dao.get_by_filters(
            source=self.SOURCE,
            source_id=source_id,
            quality="best",
            content_type=ContentTypeEnum.FILM_DICT,
        )

        if not cached_dto:
            return None
        # Make both datetime objects timezone-aware or naive consistently
        now = (
            datetime.now(timezone.utc)
            if cached_dto.updated_at.tzinfo
            else datetime.now()
        )
        if now - cached_dto.updated_at > timedelta(hours=timeout):
            return None
        logging.info(
            "Cache hit for source_id=%s,",
            source_id,
        )
        return cached_dto

    async def _create_cache_entry_if_not_exists(
        self,
        cache_dto: CacheDTO,
    ) -> CacheModel | None:
        """Helper to check existence and create cache entry."""
        cached_video = await self._cache_dao.get_by_filters(
            source=self.SOURCE,
            source_id=cache_dto.source_id,
            quality=cache_dto.quality,
        )
        if cached_video:
            return None

        created_model = await self._cache_dao.create(cache_dto)
        logging.info(
            "Successfully cached content with source_id=%s, quality=%s",
            cache_dto.source_id,
            cache_dto.quality,
        )
        return created_model

    async def send_video_from_cache(self, source_id: str, quality: str) -> bool:
        """
        Send video from cache.

        :param source_id: Source ID of the video.
        :param quality: Quality of the video.
        :return: True if video was sent from cache, False otherwise.
        """
        cached_item = await self._cache_dao.get_by_filters(
            source=self.SOURCE,
            source_id=source_id,
            quality=quality,
            content_type=ContentTypeEnum.VIDEO,
        )
        if not cached_item or not isinstance(cached_item.meta_data_dto, VideoDTO):
            return False

        await self._create_history_entry(cached_item)

        logging.info(
            "Cache hit for source_id=%s, quality=%s. Sending by file_id.",
            source_id,
            quality,
        )
        video_dto = cached_item.meta_data_dto

        if self._inline_query_id:
            await self._telegram_bot_controller.answer_inline_query_cached_video(
                inline_query_id=self._inline_query_id,
                video_dto=video_dto,
                file_id=cached_item.file_id,
            )
        else:
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_video_by_file_id(
                telegram_id=self._telegram_id,
                cache_item=cached_item,
                url=self._resolution.url,
            )
        return True

    async def _create_history_entry(
        self,
        cache_model: CacheModel | None = None,
    ) -> None:
        """
        Create a history entry for the current request.

        :param cache_model: An optional CacheModel instance if the content was cached.
        """
        user = await self._get_user_info()
        await self._history_dao.create(
            user_id=user.id,
            cache_id=cache_model.id if cache_model else None,
            source=self.SOURCE,
            url=self._resolution.url,
        )

    async def _answer_inline_query(
        self,
        video_dto: VideoDTO,
        telegram_video: Video | None,
    ) -> None:
        """
        Encapsulates the logic for responding to an inline query.

        It uses the result of the video sending attempt to determine how to answer.

        :param video_dto: The DTO containing video metadata.
        :param telegram_video: The result from the bot send operation, or None if failed
        """
        if not self._inline_query_id:
            return

        if telegram_video:
            # Success: we got a file_id, now we can answer the query.
            await self._telegram_bot_controller.answer_inline_query_cached_video(
                inline_query_id=self._inline_query_id,
                video_dto=video_dto,
                file_id=telegram_video.file_id,
            )
        elif video_dto.direct_download_url:
            # Fallback for when upload failed but we have a direct URL
            await self._telegram_bot_controller.answer_inline_query_video(
                inline_query_id=self._inline_query_id,
                video_dto=video_dto,
            )
        else:
            # Total failure: couldn't upload, no direct URL.
            # This happens if the bot is blocked by the user.
            await self._telegram_bot_controller.answer_inline_query_error(
                inline_query_id=self._inline_query_id,
                error_text=_("inline mode blocked error"),
            )

    def cleanup_files(self, dtos: Sequence[VideoDTO | PhotoDTO | AudioDTO]) -> None:
        """
        Safely deletes the downloaded files and thumbnails from a list of DTOs.

        :param dtos: List of DTOs (VideoDTO, PhotoDTO).
        """
        for item in dtos:
            if item.path:
                file_path = Path(item.path)
                if file_path.exists():
                    file_path.unlink()

            if isinstance(item, VideoDTO) and item.thumbnail:
                thumb_path = Path(item.thumbnail)
                if thumb_path.exists():
                    thumb_path.unlink()
