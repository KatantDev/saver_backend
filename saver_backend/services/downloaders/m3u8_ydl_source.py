from typing import Any, ClassVar
from urllib.parse import urlparse

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class M3U8YdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Rutube through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.M3U8_YDL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for Rutube."""
        super().__init__(*args, **kwargs)

        params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(params)

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info from m3u8 url.

        :param url: The url to get video info from.
        :return: The video info or None.
        """
        result = await super().get_video_info(url=url)
        url_path = urlparse(self._resolution.url).path[:200]
        if result is None or self._video is None:
            return None

        self._video = self._video.model_copy(
            update={"title": url_path, "source_id": url_path},
        )
        result.update(
            {
                "id": self._video.source_id,
                "title": self._video.title,
                "fulltitle": self._video.title,
            },
        )

        return result
