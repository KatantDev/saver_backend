from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class VKClipsYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from VK Clips through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.VK_CLIPS_YDL
    COOKIES: ClassVar[bool] = True
    DIRECT_URL_DOWNLOAD: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with custom yt-dlp parameters for VK."""
        super().__init__(*args, **kwargs)

        vk_params = {
            "format": "best[protocol!=https][width<=1080]",
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(vk_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=vk_params["format"],
        )
