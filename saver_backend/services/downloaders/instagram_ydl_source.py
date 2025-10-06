import logging
from typing import Any, ClassVar, Dict

from yt_dlp import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class InstagramYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Instagram through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.INSTAGRAM_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._yt_dlp.params["format"] = "best"

    async def get_video_info(self, url: str) -> Dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information.
        """
        try:
            return await self._get_video_info(url)
        except DownloadError as error:
            if "No video formats found" in error.msg:
                return None
            raise error

    async def download_video(self) -> None:
        """
        Asynchronously downloads a video from Instagram.

        :return: Dictionary with information about the downloaded file.
        """
        video_info = await self.get_video_info(url=self._resolution.url)

        if video_info is None:
            logging.error(
                "%s | Error getting video information (%s)",
                self.SOURCE,
                self._resolution.url,
            )

        logging.info(
            "%s | Starting video download: %s",
            self.SOURCE,
            self._resolution.url,
        )

        await self._download_video(url_list=[self._resolution.url])
