import logging
from typing import Any, ClassVar, Dict

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.exceptions import TikTokYtDlpDownloaderError
from saver_backend.services.downloaders.ydl_source import YtDlpController


class TikTokYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from TikTok through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.TIKTOK

    def __init__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._yt_dlp.params["format"] = "bv*+ba/best"

    async def get_video_info(self, url: str) -> Dict[str, Any] | None:
        """Override to raise a specific error for slideshows (which return no info)."""
        info = await super()._get_video_info(url)
        if not info:
            raise TikTokYtDlpDownloaderError
        return info

    async def download_video(self) -> None:
        """Public method to start the download process."""
        try:
            await self._download_and_send_video()
        except TikTokYtDlpDownloaderError:
            logging.warning(
                "Could not get info for TikTok URL %s. It might be a slideshow.",
                self._resolution.url,
            )
            await self._telegram_bot_controller.send_tiktok_error_downloading(
                telegram_id=self._telegram_id,
            )
