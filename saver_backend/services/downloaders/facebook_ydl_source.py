from typing import Any, ClassVar

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController

# Cap on the smaller side: «height<=1080» dropped 720x1280 and 1080x1920 of a
# vertical reel and left only 540x960 (task 20)
FACEBOOK_MAX_RESOLUTION = 1080


class FacebookYdlController(YtDlpController):
    """Controller for downloading videos from Facebook via yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.FACEBOOK_YDL
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.ALL
    COOKIES: ClassVar[bool] = False
    # Prod 21-23.11: some proxies time out on facebook.com while the server's
    # own address opens the same reel, so try another proxy, then go direct.
    RETRIES_ON_ERROR: ClassVar[int] = 1
    DIRECT_FALLBACK: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with optimal yt-dlp parameters for Facebook."""
        super().__init__(*args, **kwargs)

        facebook_params = {
            "format": "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]",
            "format_sort": [f"res:{FACEBOOK_MAX_RESOLUTION}"],
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(facebook_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=facebook_params["format"],
        )
