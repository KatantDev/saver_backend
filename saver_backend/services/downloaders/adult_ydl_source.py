from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class AdultYdlController(YtDlpController):
    """Generic controller for downloading videos from adult sites via yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.ADULT_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with optimal yt-dlp parameters for video sites."""
        super().__init__(*args, **kwargs)

        adult_params = {
            "format": "best",
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(adult_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=adult_params["format"],
        )
