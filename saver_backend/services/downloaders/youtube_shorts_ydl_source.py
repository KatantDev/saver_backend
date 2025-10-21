from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class YouTubeShortsYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from YT Shorts through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YOUTUBE_SHORTS_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
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

    async def download_video(self) -> None:
        """Public method to start the download process."""
        await self._download_and_send_video()
