import asyncio
import logging
import secrets
from abc import ABC
from pathlib import Path
from typing import Any, ClassVar, Dict

import sentry_sdk
import yt_dlp
from aiogram.types import Video
from yt_dlp import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.exceptions import (
    IPAddressBlockedError,
    VideoInfoNotSetError,
)
from saver_backend.services.downloaders.schema import (
    VideoCacheDTO,
    VideoDTO,
)
from saver_backend.settings import settings


class YtDlpController(BaseSourceController, ABC):
    """Asynchronous controller for downloading videos through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED
    COOKIES: ClassVar[bool] = False
    SUPPORTS_STREAMING: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._video: VideoDTO | None = None
        self._retries: int = 0

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
        self._set_cookies()

        self._download_directory.mkdir(parents=True, exist_ok=True)
        self._yt_dlp = self._create_yt_dlp(self._base_options)

    def _create_yt_dlp(self, params: dict[str, Any]) -> yt_dlp.YoutubeDL:
        controller = yt_dlp.YoutubeDL(params)
        controller.add_progress_hook(self._progress_hook)
        return controller

    def _set_proxy(self) -> None:
        proxy = secrets.choice(settings.proxies)
        params = {**self._yt_dlp.params, "proxy": f"socks5://{proxy}"}
        self._yt_dlp = self._create_yt_dlp(params)

    @property
    def video_info(self) -> VideoDTO:
        """
        Get the video information.

        :return: The video information.
        """
        if not self._video:
            raise VideoInfoNotSetError
        return self._video

    def _set_cookies(self) -> None:
        if not self.COOKIES:
            return

        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        cookie_dir = base_dir / "cookies" / self.SOURCE.value
        cookie_files = list(cookie_dir.glob("cookies*.txt"))
        if not cookie_files:
            logging.error(
                "Cookies enabled for %s, but no files found in %s",
                self.SOURCE,
                cookie_dir,
            )
            return

        cookie_file = secrets.choice(cookie_files).resolve()
        self._base_options["cookiefile"] = str(cookie_file)
        logging.info("Using cookie file %s for %s", cookie_file, self.SOURCE)

    async def download_video(self) -> None:
        """
        Download a video, checking the cache first.

        If a cached version (file_id) exists, it sends it directly.
        Otherwise, it proceeds with the full download process.
        """
        info_dict = await self.get_video_info(url=self._resolution.url)
        if not self._video or not info_dict:
            if self._message_id:
                await self._telegram_bot_controller.bot.delete_message(
                    chat_id=self._telegram_id,
                    message_id=self._message_id,
                )
            return

        if not self._video.source_id:
            logging.error("Could not determine source_id.")
            return

        is_sent_from_cache = await self.send_video_from_cache(
            source_id=self._video.source_id,
        )
        if is_sent_from_cache:
            return

        logging.info(
            "Cache miss for source_id=%s. Starting download.",
            self._video.source_id,
        )
        await self._execute_download(info_dict)

    async def _execute_download(self, info_dict: dict[str, Any]) -> None:
        """
        Executes the actual download and sending logic.

        Subclasses can override this to add specific error handling.

        :param info_dict: The dictionary from yt_dlp.extract_info.
        """
        self._process_percent(percent=16)

        try:
            await asyncio.to_thread(self._yt_dlp.process_info, info_dict)
        except Exception as e:
            if settings.environment == "local":
                logging.error("yt-dlp download process failed: %s", e, exc_info=True)
            sentry_sdk.capture_exception(e)
            await self.delete_processing_message()
            return

        if not self._video or not self._video.path:
            logging.error("Could not determine final filepath from yt-dlp result.")
            return

        if not Path(self._video.path).exists():
            logging.error(
                "yt-dlp reported success, but file %s does not exist.",
                self._video,
            )
            return

        await self._send_and_cache_video()

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

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        if self._retries > 3:
            raise IPAddressBlockedError
        self._retries += 1

        try:
            info_dict = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=url,
                download=False,
            )

            video_id = info_dict.get("id")
            video_ext = info_dict.get("ext")

            predicted_path = self._download_directory / f"{video_id}.{video_ext}"

            thumbnail = self._get_thumbnail(source_id=video_id)

            video = VideoDTO.from_yt_dlp(
                info=info_dict,
                file_path=predicted_path,
                thumbnail_path=thumbnail,
            )
            self._video = video

            return info_dict
        except DownloadError as e:
            if (
                "Your IP address is blocked from accessing this post" in e.msg
                or "Unable to connect to proxy" in e.msg
                or "SOCKS server failure" in e.msg
            ):
                self._set_proxy()
                return await self.get_video_info(url=url)
            if settings.environment == "local":
                logging.exception(e)
            sentry_sdk.capture_exception(e)
        return None

    async def _send_and_cache_video(self) -> None:
        """Sends the video to the user and then caches the result."""
        if not self._video:
            logging.error("Cannot send video: self._video is not set.")
            return

        telegram_video = await self._telegram_bot_controller.send_finish_downloading(
            video=self._video,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
            supports_streaming=self.SUPPORTS_STREAMING,
        )

        if telegram_video:
            if not self._video.source_id:
                logging.warning("Cannot cache video: source_id is missing.")
                return

            logging.info(
                "Attempting to cache video with source_id=%s",
                self._video.source_id,
            )
            await self._save_to_cache(telegram_video)

    async def _save_to_cache(
        self,
        telegram_video: Video,
    ) -> None:
        """
        Save video details to cache.

        :param telegram_video: The Video object from aiogram after sending.
        """
        if not self._video:
            logging.warning("Cannot save to cache: self._video is not set.")
            return

        video_cache = VideoCacheDTO.from_yt_dlp(
            source=self.SOURCE,
            telegram_video=telegram_video,
            video=self._video,
        )
        if not video_cache:
            return

        try:
            await self._video_cache_dao.create(video_cache=video_cache)
            logging.info(
                "Successfully cached video with source_id=%s",
                self._video.source_id,
            )
        except Exception as e:
            logging.error(
                "Failed to save video cache for source_id=%s: %s",
                self._video.source_id,
                e,
                exc_info=True,
            )
            sentry_sdk.capture_exception(e)

    def _get_thumbnail(self, source_id: str | None) -> Path | None:
        if not source_id:
            return None

        possible_extensions = (".webp", ".png", ".jpg")
        for ext in possible_extensions:
            thumb_path = self._download_directory / f"{source_id}{ext}"
            if not thumb_path.exists():
                continue
            return thumb_path
        return None
