import logging
from typing import Any, ClassVar

from yt_dlp import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class YouTubeShortsYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from YT Shorts through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YOUTUBE_SHORTS_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with custom yt-dlp parameters for YouTube."""
        super().__init__(*args, **kwargs)

        youtube_params = {
            "format": "bestvideo[ext=mp4][height<=1080]+bestaudio/best[ext=mp4]",
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
            "extractor_args": {
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://saver_backend-bgutil:4416"],
                },
            },
        }
        self._yt_dlp.params.update(youtube_params)

    async def _handle_download_error(self, error: DownloadError) -> None:
        """
        Handle YouTube-specific download errors.

        Catches errors for unavailable videos (deleted, terminated account, etc.).

        :param error: The DownloadError exception instance.
        """
        if "Video unavailable" in str(error):
            logging.warning(
                "Handled unavailable YouTube Shorts for URL: %s. Reason: %s",
                self._resolution.url,
                str(error).strip(),
            )
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
        else:
            await super()._handle_download_error(error)
