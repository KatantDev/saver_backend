import logging
from typing import Any, ClassVar, Dict

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.exceptions import TikTokYtDlpDownloaderError
from saver_backend.services.downloaders.ydl_source import YtDlpController


class TikTokYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from TikTok through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.TIKTOK

    def __init__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._yt_dlp.params["format"] = "bv*+ba/best"

    async def get_video_info(self, url: str) -> Dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information.
        """
        return await self._get_video_info(url)

    async def download_video(self) -> None:
        """
        Asynchronously downloads a video from TikTok.

        :return: Dictionary with information about the downloaded file.
        """
        # Get video information
        video_info = await self.get_video_info(url=self._resolution.url)

        if video_info is None:
            logging.error(
                "%s | Error getting video information (%s)",
                self.SOURCE,
                self._resolution.url,
            )
            raise TikTokYtDlpDownloaderError

        logging.info(
            "%s | Starting video download: %s",
            self.SOURCE,
            self._resolution.url,
        )

        # Execute download in a separate thread
        await self._download_video(url_list=[self._resolution.url])
