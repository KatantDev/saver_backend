from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class PinterestYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Pinterest through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.PINTEREST_YDL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for Pinterest."""
        super().__init__(*args, **kwargs)

        pinterest_params = {
            "format": "bestvideo+bestaudio",
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(pinterest_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=pinterest_params["format"],
        )

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info, with specific handling for private or unavailable pins.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        return await super().get_video_info(url)
