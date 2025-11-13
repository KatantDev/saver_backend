from typing import Any, ClassVar

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class FacebookYdlController(YtDlpController):
    """Controller for downloading videos from Facebook via yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.FACEBOOK_YDL
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.ALL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with optimal yt-dlp parameters for Facebook."""
        super().__init__(*args, **kwargs)

        facebook_params = {
            "format": "bestvideo[ext=mp4][height<=1080]+bestaudio/best[ext=mp4]",
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(facebook_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=facebook_params["format"],
        )
