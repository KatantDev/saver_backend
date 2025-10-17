import logging
from typing import Any, ClassVar, Dict

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.exceptions import YtDlpDownloaderError
from saver_backend.services.downloaders.ydl_source import YtDlpController


class YouTubeShortsYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from YouTube Shorts through yt-dlp."""

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
            # Use the download directory already defined in the parent class
            "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best",
            "no_warnings": True,
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(youtube_params)

    async def get_video_info(self, url: str) -> Dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information.
        """
        return await self._get_video_info(url)

    async def download_video(self) -> None:
        """
        Asynchronously downloads a video from YouTube Shorts.

        This method gets video info first and then proceeds with the download.
        :raises YtDlpDownloaderError: If video information cannot be retrieved.
        """
        # Get video information
        video_info = await self.get_video_info(url=self._resolution.url)

        if video_info is None:
            logging.error(
                "%s | Error getting video information (%s)",
                self.SOURCE,
                self._resolution.url,
            )
            raise YtDlpDownloaderError

        logging.info(
            "%s | Starting video download: %s",
            self.SOURCE,
            self._resolution.url,
        )

        await self._download_video(url_list=[self._resolution.url])
