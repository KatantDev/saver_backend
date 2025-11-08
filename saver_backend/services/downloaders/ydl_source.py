import asyncio
import logging
import secrets
from abc import ABC
from pathlib import Path
from typing import Any, ClassVar, Dict

import sentry_sdk
import yt_dlp
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.exceptions import (
    IPAddressBlockedError,
    VideoInfoNotSetError,
)
from saver_backend.services.downloaders.schema import (
    VideoDTO,
)
from saver_backend.settings import settings


class YtDlpController(BaseSourceController, ABC):
    """Asynchronous controller for downloading videos through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED
    COOKIES: ClassVar[bool] = False
    SUPPORTS_STREAMING: ClassVar[bool] = True
    DIRECT_URL_DOWNLOAD: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._video: VideoDTO | None = None

        # Get download directory and create it
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)

        self._base_options: dict[str, Any] = {
            "proxy": self._proxy,
            "format": "best",
            "outtmpl": str(self._download_directory / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "writethumbnail": True,
            "quiet": True,
            "noprogress": True,
            "overwrites": True,
            "postprocessor_args": [
                "-nostdin",
            ],
        }
        if self._selected_format_id:
            self._base_options["format"] = self._selected_format_id

        # Set cookies if needed and initialize yt-dlp controller
        self._set_cookies()
        self._yt_dlp = self._create_yt_dlp(self._base_options)

    async def close(self) -> None:
        """Close the HTTP client."""
        self._yt_dlp.close()

    def _create_yt_dlp(self, params: dict[str, Any]) -> yt_dlp.YoutubeDL:
        if hasattr(self, "_yt_dlp"):
            self._yt_dlp.close()

        controller = yt_dlp.YoutubeDL(params)
        controller.add_progress_hook(self._progress_hook)
        return controller

    def _set_proxy(self) -> None:
        try:
            self._proxy = self._proxies.pop(0)
        except IndexError as error:
            raise IPAddressBlockedError from error
        self._yt_dlp = self._create_yt_dlp(
            {
                **self._yt_dlp.params,
                "proxy": self._proxy,
            },
        )

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
        # Получаем информацию о видео, в случае, если данные не получены - останавливаем
        try:
            info_dict = await self.get_video_info(url=self._resolution.url)
        except DownloadError as e:
            logging.exception(e)
            await self._send_error_message()
            return

        if not self._video or not info_dict:
            return

        if not self._video.source_id:
            logging.error("Could not determine source_id.")
            return

        # Get the video from the cache if it was previously downloaded
        cache_quality_key = self._selected_format_id or "best"
        is_sent_from_cache = await self.send_video_from_cache(
            source_id=self._video.source_id,
            quality=cache_quality_key,
        )
        if is_sent_from_cache:
            return

        logging.info(
            "Cache miss for source_id=%s and quality=%s. Starting download.",
            self._video.source_id,
            cache_quality_key,
        )

        if self._video.direct_download_url:
            logging.info("Attempting direct URL send for source %s.", self.SOURCE)
            await self._send_video(
                self._video,
                supports_streaming=self.SUPPORTS_STREAMING,
            )
        else:
            logging.info("Starting full download for source %s.", self.SOURCE)
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
        except DownloadError as e:
            error_message = str(e)
            if "HTTP Error 403: Forbidden" in error_message:
                logging.error("YouTube download failed due to expired cookies (403).")
            raise e
        except Exception as e:
            await self._send_error_message()
            if settings.environment == "local":
                logging.exception(e)
            sentry_sdk.capture_exception(e)

        if not self._video or not self._video.path:
            await self._send_error_message()
            logging.error("Could not determine final filepath from yt-dlp result.")
            return

        if not Path(self._video.path).exists():
            await self._send_error_message()
            logging.error(
                "yt-dlp reported success, but file %s does not exist.",
                self._video.path,
            )
            return

        thumbnail = self._get_thumbnail(source_id=self._video.source_id)
        self._video = self._video.model_copy(update={"thumbnail": thumbnail})

        await self._send_video(
            video_dto=self._video,
            supports_streaming=self.SUPPORTS_STREAMING,
        )
        self._cleanup_files()

    def _progress_hook(self, d: Dict[str, Any]) -> None:
        """This hook's only job is to report download progress."""
        if d["status"] == "downloading":
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded_bytes = d.get("downloaded_bytes")
            if not total_bytes or not downloaded_bytes or total_bytes <= 0:
                return

            percent_float = (downloaded_bytes / total_bytes) * 100
            percent = round(percent_float * 0.66 + 16)

            if (
                self._last_percent + 10 >= percent >= self._last_percent - 10
                and self._message_id is not None
            ):
                return

            self._process_percent(percent=percent)

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        try:
            info_dict = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=url,
                download=False,
            )

            video_id = info_dict.get("id")
            video_ext = info_dict.get("ext")

            predicted_path = self._download_directory / f"{video_id}.{video_ext}"

            self._video = VideoDTO.from_yt_dlp(
                info=info_dict,
                file_path=predicted_path,
                extract_direct_links=self.DIRECT_URL_DOWNLOAD,
                quality=self._selected_format_id or "best",
            )

            return info_dict
        except DownloadError as e:
            if (
                "Your IP address is blocked from accessing this post" in e.msg
                or "Unable to connect to proxy" in e.msg
                or "SOCKS server failure" in e.msg
            ):
                self._set_proxy()
                return await self.get_video_info(url=url)
            raise

    def _cleanup_files(self) -> None:
        """Safely deletes the downloaded video and thumbnail files."""
        if not self._video:
            return

        if self._video.path and self._video.path.exists():
            self._video.path.unlink(missing_ok=True)

        if self._video.thumbnail:
            thumb_path = Path(self._video.thumbnail)
            if thumb_path.exists():
                thumb_path.unlink(missing_ok=True)

    def _get_thumbnail(self, source_id: str | None) -> Path | None:
        if not source_id:
            return None

        possible_extensions = (".webp", ".png", ".jpg", ".jpeg", ".image")
        for ext in possible_extensions:
            thumb_path = self._download_directory / f"{source_id}{ext}"
            if not thumb_path.exists():
                continue
            return thumb_path
        return None
