from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class VKClipsYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from VK Clips through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.VK_CLIPS_YDL
    COOKIES: ClassVar[bool] = False
    DIRECT_URL_DOWNLOAD: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with custom yt-dlp parameters for VK."""
        super().__init__(*args, **kwargs)

        vk_params = {
            "format": "bestvideo[ext=mp4][height<=1080]+bestaudio/best[ext=mp4]",
        }
        self._yt_dlp.params.update(vk_params)
