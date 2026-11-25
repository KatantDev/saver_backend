from typing import Any, ClassVar

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.downloaders.vk_common import VKAccessMixin
from saver_backend.services.downloaders.vk_ydl_patch import apply_vk_subtitle_patch
from saver_backend.services.downloaders.ydl_source import YtDlpController

apply_vk_subtitle_patch()


class VKVideoYdlController(VKAccessMixin, YtDlpController):
    """Asynchronous controller for downloading videos from VK Video through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.VK_VIDEO_YDL
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.RU
    COOKIES: ClassVar[bool] = False
    # VK's file servers fail for minutes at a time («The downloaded file is
    # empty», HTTP 500: 7 on prod on 24.11); the same video downloads later
    RETRIES_ON_ERROR: ClassVar[int] = 1

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for VK Video."""
        super().__init__(*args, **kwargs)

        vk_params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(vk_params)

        if self._selected_format_id is None:
            # Streams first; some videos have only plain https files (url144 ...
            # url720) and without the second choice they failed with «Requested
            # format is not available» (44 on prod on 21-23.11).
            vk_params["format"] = "best[protocol!=https]/best"
            self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
                format_spec=vk_params["format"],
            )
