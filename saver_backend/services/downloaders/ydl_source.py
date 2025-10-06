import asyncio
import logging
import secrets
from abc import ABC
from pathlib import Path
from typing import Any, Awaitable, ClassVar, Dict

import sentry_sdk
import yt_dlp

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.settings import settings


class YtDlpController(BaseSourceController, ABC):
    """Asynchronous controller for downloading videos through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._base_options: dict[str, Any] = {
            "format": "best",
            "outtmpl": str(self._download_directory / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "extract_flat": False,
            "writethumbnail": True,
            "writeinfojson": False,
            "ignoreerrors": False,
            "no_warnings": False,
            "quiet": True,
            "noprogress": True,
            "verbose": False,
        }
        if self.COOKIES:
            self._list_cookies = Path(f"cookies/{self.SOURCE.value}").glob(
                "cookies*.txt",
            )
            cookie_file = secrets.choice(list(self._list_cookies)).resolve()
            self._base_options["cookiefile"] = str(cookie_file)
            logging.info("Using cookie file %s for %s", cookie_file, self.SOURCE)

        self._download_directory.mkdir(exist_ok=True)

        self._yt_dlp = yt_dlp.YoutubeDL(self._base_options)
        self._loop = asyncio.get_event_loop()
        self._filename: Path | None = None
        self._yt_dlp.add_progress_hook(self._progress_hook)

        self._message_id: int | None = None
        self._last_percent: int = 0

    async def _download_video(self, url_list: list[str]) -> Dict[str, Any] | None:
        """
        Download video in separate thread.

        :param url_list: List of URLs of the videos.
        :return: Dictionary with video information.
        """
        try:
            return await asyncio.to_thread(
                self._yt_dlp.download,
                url_list=url_list,
            )
        except Exception as e:
            if settings.environment == "local":
                logging.exception(e)
            sentry_sdk.capture_exception(e)
            return None

    async def _get_video_info(self, url: str) -> Dict[str, Any] | None:
        """
        Get video information without downloading in separate thread.

        :param url: URL of the video.
        :return: Dictionary with video information.
        """
        try:
            return await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=url,
                download=False,
            )
        except Exception as e:
            if settings.environment == "local":
                logging.exception(e)
            sentry_sdk.capture_exception(e)
            return None

    def _process_message(self, title: str, percent: int) -> None:
        """
        Process message.

        :param title: Title of the video.
        :param percent: Percent of the video.
        """
        coro: Awaitable[int | None] | None = None
        if self._message_id is None:
            coro = self._telegram_bot_controller.send_start_downloading(
                telegram_id=self._telegram_id,
                title=title or "",
                percent=percent,
            )
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            try:
                message_id = future.result(timeout=5)
                if message_id:
                    self._message_id = message_id
            except TimeoutError:
                logging.warning(
                    "Timeout waiting for start message to be sent (title=%r)",
                    title,
                )
        else:
            coro = self._telegram_bot_controller.send_update_downloading(
                telegram_id=self._telegram_id,
                message_id=self._message_id,
                title=title or "",
                percent=percent,
            )
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _send_finish_message(
        self,
        video: VideoDTO,
    ) -> None:
        """
        Send finish message.

        :param video: Video.
        """
        if self._filename is None:
            return

        coro = self._telegram_bot_controller.send_finish_downloading(
            video=video,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
        )
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _progress_hook(self, d: Dict[str, Any]) -> None:
        """
        Progress handler for download.

        :param d: Dictionary with information about the progress.
        """
        filename: str | None = d.get("filename")
        info: dict[str, Any] = d.get("info_dict") or {}
        title: str | None = (
            info.get("description") or info.get("fulltitle") or info.get("title")
        )
        percent: int | float | None = d.get("_percent")

        if d["status"] == "finished" and filename:
            self._filename = Path(filename)

            width = info.get("width")
            height = info.get("height")
            duration = info.get("duration")
            video = VideoDTO(
                path=self._filename,
                title=title or "",
                width=int(width) if width else None,
                height=int(height) if height else None,
                duration=int(duration) if duration else None,
                thumbnail=next(
                    (
                        t.get("filepath") or t.get("filename")
                        for t in info.get("thumbnails", [])
                        if t.get("filepath") or t.get("filename")
                    ),
                    None,
                ),
            )
            self._send_finish_message(video=video)
            return

        if d["status"] == "downloading" and filename:
            self._filename = Path(filename)

        if percent is None:
            return

        if self._last_percent + 10 >= percent and self._message_id is not None:
            return

        self._last_percent = int(percent)
        self._process_message(title=title or "", percent=int(percent))

    def _get_downloaded_file(self) -> Path | None:
        """
        Find the downloaded file by title.

        :return: Path to the downloaded file.
        """
        if self._filename and self._filename.exists():
            return self._filename

        return None
