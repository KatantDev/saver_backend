import asyncio
import logging
import secrets
from abc import ABC
from pathlib import Path
from typing import Any, ClassVar, Dict

import sentry_sdk
import yt_dlp
from aiogram.types import Video
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from saver_backend.db.dao.video_cache_dao import VideoCacheDAO
from saver_backend.entities.enums import SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.settings import settings


class YtDlpController(BaseSourceController, ABC):
    """Asynchronous controller for downloading videos through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED
    COOKIES: ClassVar[bool] = False
    SUPPORTS_STREAMING: ClassVar[bool] = True

    def __init__(
        self,
        *args: Any,
        video_cache_dao: "VideoCacheDAO",
        session_factory: async_sessionmaker[AsyncSession],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._video_cache_dao = video_cache_dao
        self._session_factory = session_factory
        self._video_info: dict[str, Any] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._base_options: dict[str, Any] = {
            "format": "best",
            "outtmpl": str(self._download_directory / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "writethumbnail": True,
            "quiet": True,
            "noprogress": False,
            "overwrites": True,
            "postprocessor_args": [
                "-nostdin",
            ],
        }
        if settings.source_ip:
            self._base_options["source_address"] = settings.source_ip
        if self.COOKIES:
            base_dir = Path(__file__).resolve().parent.parent.parent.parent
            cookie_dir = base_dir / "cookies" / self.SOURCE.value
            cookie_files = list(cookie_dir.glob("cookies*.txt"))
            if cookie_files:
                cookie_file = secrets.choice(cookie_files).resolve()
                self._base_options["cookiefile"] = str(cookie_file)
                logging.info("Using cookie file %s for %s", cookie_file, self.SOURCE)
            else:
                logging.warning(
                    "Cookies enabled for %s, but no files found in %s",
                    self.SOURCE,
                    cookie_dir,
                )

        self._download_directory.mkdir(parents=True, exist_ok=True)
        self._yt_dlp = yt_dlp.YoutubeDL(self._base_options)
        self._yt_dlp.add_progress_hook(self._progress_hook)

    async def download_video(self) -> None:
        """
        Download a video, checking the cache first.

        If a cached version (file_id) exists, it sends it directly.
        Otherwise, it proceeds with the full download process.
        """
        info = await self._get_video_info(url=self._resolution.url)
        if not info:
            if self._message_id:
                await self._telegram_bot_controller.bot.delete_message(
                    chat_id=self._telegram_id,
                    message_id=self._message_id,
                )
            return

        source_id = info.get("id")
        if not source_id:
            logging.error("Could not determine source_id from info_dict.")
            return

        cached_video = await self._video_cache_dao.get_by_source_id(
            source=self.SOURCE,
            source_id=source_id,
        )
        if cached_video:
            logging.info("Cache hit for source_id=%s. Sending by file_id.", source_id)
            if self._message_id:
                await self._telegram_bot_controller.bot.delete_message(
                    chat_id=self._telegram_id,
                    message_id=self._message_id,
                )
            await self._telegram_bot_controller.send_video_by_file_id(
                telegram_id=self._telegram_id,
                file_id=cached_video.file_id,
                url=self._resolution.url,
            )
            return

        logging.info("Cache miss for source_id=%s. Starting download.", source_id)
        await self._execute_download(info)

    async def _execute_download(self, info: dict[str, Any]) -> None:
        """Executes the actual download and sending logic.

        Subclasses can override this to add specific error handling.
        """
        self._process_percent(percent=16)

        try:
            await asyncio.to_thread(self._yt_dlp.process_info, info)
        except Exception as e:
            logging.error("yt-dlp download process failed: %s", e, exc_info=True)
            sentry_sdk.capture_exception(e)
            if self._message_id:
                await self._telegram_bot_controller.bot.delete_message(
                    chat_id=self._telegram_id,
                    message_id=self._message_id,
                )
            return

        video_id = info.get("id")
        video_ext = info.get("ext")
        if not video_id or not video_ext:
            logging.error("Could not determine final filepath from info_dict.")
            return

        final_filepath = self._download_directory / f"{video_id}.{video_ext}"
        if not final_filepath.exists():
            logging.error(
                "Download reported as finished, but file %s does not exist.",
                final_filepath,
            )
            return

        video_dto = self._build_video_dto(info, final_filepath)
        await self._send_and_cache_video(video_dto)

    def _progress_hook(self, d: Dict[str, Any]) -> None:
        """This hook's only job is to report download progress."""
        if d["status"] == "downloading":
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded_bytes = d.get("downloaded_bytes")
            if not total_bytes or not downloaded_bytes:
                return

            percent_float = (downloaded_bytes / total_bytes) * 100
            percent = round(percent_float * 0.66 + 16)

            if self._last_percent + 10 >= percent and self._message_id is not None:
                return

            self._process_percent(percent=percent)

    async def _get_video_info(self, url: str) -> Dict[str, Any] | None:
        """Get video information and store it in the instance."""
        try:
            info = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=url,
                download=False,
            )
            self._video_info = info
            return info
        except Exception as e:
            if settings.environment == "local":
                logging.exception(e)
            sentry_sdk.capture_exception(e)
            return None

    async def _send_and_cache_video(self, video: VideoDTO) -> None:
        """
        Send the finished video to the user and schedule caching.

        :param video: The video data transfer object.
        """
        telegram_video = await self._telegram_bot_controller.send_finish_downloading(
            video=video,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
            supports_streaming=self.SUPPORTS_STREAMING,
        )
        if telegram_video:
            task = asyncio.create_task(self._save_to_cache(telegram_video))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            logging.info("Scheduled caching for video source_id=%s", video.source_id)

    async def _save_to_cache(self, telegram_video: Video) -> None:
        """
        Save video details to cache.

        :param telegram_video: The Video object from aiogram after sending.
        """
        if not self._video_info:
            logging.warning("Cannot save to cache: self._video_info is not set.")
            return

        source_id = self._video_info.get("id")
        if not source_id:
            logging.warning("Cannot save to cache: source_id not found in video_info.")
            return

        async with self._session_factory() as session:
            video_cache_dao = VideoCacheDAO(session)

            metadata_to_save = {
                "title": self._video_info.get("title"),
                "quality": "best",
                "duration": self._video_info.get("duration"),
            }
            try:
                await video_cache_dao.create(
                    source=self.SOURCE,
                    source_id=source_id,
                    file_id=telegram_video.file_id,
                    file_unique_id=telegram_video.file_unique_id,
                    meta_data=metadata_to_save,
                )
                await session.commit()  #! Коммитим изменения
                logging.info("Successfully cached video with source_id=%s", source_id)
            except Exception as e:
                logging.error(
                    "Failed to save video cache for source_id=%s: %s",
                    source_id,
                    e,
                    exc_info=True,
                )
                await session.rollback()  #! Откатываем в случае ошибки
                sentry_sdk.capture_exception(e)

    def _get_thumbnail(self, video_id: str | None) -> Path | None:
        if not video_id:
            return None

        possible_extensions = (".webp", ".png", ".jpg")
        for ext in possible_extensions:
            thumb_path = self._download_directory / f"{video_id}{ext}"
            if not thumb_path.exists():
                continue
            return thumb_path
        return None

    def _build_video_dto(self, info: dict[str, Any], file_path: Path) -> VideoDTO:
        """Helper to construct VideoDTO from yt-dlp info and file path."""
        video_id = info.get("id")
        thumbnail = self._get_thumbnail(video_id=video_id)
        width = info.get("width")
        height = info.get("height")
        duration = info.get("duration")

        return VideoDTO(
            path=file_path,
            source_id=video_id,
            title=info.get("title", ""),
            width=int(width) if width else None,
            height=int(height) if height else None,
            duration=int(duration) if duration else None,
            thumbnail=thumbnail,
            url=self._resolution.url,
        )
