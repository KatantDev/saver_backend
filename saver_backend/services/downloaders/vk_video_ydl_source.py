from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class VKVideoYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from VK Video through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.VK_VIDEO_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for VK Video."""
        super().__init__(*args, **kwargs)

        vk_params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(vk_params)

        if self._selected_format_id is None:
            vk_params["format"] = "best[protocol!=https]"
            self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
                format_spec=vk_params["format"],
            )
