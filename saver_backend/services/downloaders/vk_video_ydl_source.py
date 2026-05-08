from typing import Any, ClassVar

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class VKVideoYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from VK Video through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.VK_VIDEO_YDL
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.RU
    COOKIES: ClassVar[bool] = False

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
        Get video info, with specific handling for private/restricted videos.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        try:
            return await super().get_video_info(url)
        except DownloadError as e:
            if "Access restricted" in str(e):
                await self.delete_processing_message()
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
                return None
            raise
