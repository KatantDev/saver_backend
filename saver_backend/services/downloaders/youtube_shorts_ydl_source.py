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
            "js_runtimes": {
                "node": {
                    "enabled": True,
                },
            },
        }
        self._yt_dlp.params.update(youtube_params)

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info, with specific handling for private/restricted videos.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        try:
            return await super().get_video_info(url)
        except DownloadError as e:
            if "Video unavailable" in str(e):
                await self.delete_processing_message()
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
                return None
            raise
