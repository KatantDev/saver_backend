from typing import Any, ClassVar

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.exceptions import VideoIsPrivateError
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

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video information, handling private/restricted access errors.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        :raises VideoIsPrivateError: If the video access is restricted.
        """
        try:
            return await super().get_video_info(url)
        except DownloadError as e:
            if "Access restricted" in str(e):
                raise VideoIsPrivateError from e
            raise
