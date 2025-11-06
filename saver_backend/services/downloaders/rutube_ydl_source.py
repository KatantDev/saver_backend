from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class RutubeYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Rutube through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.RUTUBE_YDL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for Rutube."""
        super().__init__(*args, **kwargs)

        rutube_params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(rutube_params)
