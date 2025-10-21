import asyncio
import logging
import secrets
from abc import ABC
from pathlib import Path
from typing import Any, ClassVar, Dict

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
    SUPPORTS_STREAMING: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
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
        """Get video information without downloading in a separate thread."""
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

    def _send_finish_message(self, video: VideoDTO) -> None:
        """Send the finished video to the user."""
        coro = self._telegram_bot_controller.send_finish_downloading(
            video=video,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
            supports_streaming=self.SUPPORTS_STREAMING,
        )
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _download_and_send_video(self) -> None:
        """Run the full, reliable download-and-send logic.

        This protected method contains the complete download process.
        """
        info = await self._get_video_info(url=self._resolution.url)
        if not info:
            logging.error(
                "Failed to get video info for %s. Aborting download.",
                self._resolution.url,
            )
            return

        self._process_percent(percent=16)

        try:
            await asyncio.to_thread(self._yt_dlp.download, [self._resolution.url])
        except Exception as e:
            logging.error("yt-dlp download process failed: %s", e)
            sentry_sdk.capture_exception(e)
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

        thumbnail = None
        video_id = info.get("id")
        if video_id:
            possible_extensions = ".webp"
            for ext in possible_extensions:
                thumb_path = self._download_directory / f"{video_id}{ext}"
                if thumb_path.exists():
                    thumbnail = str(thumb_path)
                    break

        width = info.get("width")
        height = info.get("height")
        duration = info.get("duration")

        video = VideoDTO(
            path=final_filepath,
            title=info.get("title", ""),
            width=int(width) if width else None,
            height=int(height) if height else None,
            duration=int(duration) if duration else None,
            thumbnail=thumbnail,
            url=self._resolution.url,
        )
        self._send_finish_message(video)
