from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController

# Cap on the smaller side: «height<=1080» dropped 1080x1920 of a vertical
# short and left 480x852 (task 20)
DZEN_MAX_RESOLUTION = 1080


class DzenYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Dzen Video through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.DZEN_YDL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for Dzen Video."""
        super().__init__(*args, **kwargs)

        dzen_params = {
            "format": "bestvideo+bestaudio/best",
            "format_sort": [f"res:{DZEN_MAX_RESOLUTION}"],
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(dzen_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=dzen_params["format"],
        )
