import logging
from typing import TYPE_CHECKING, Any, ClassVar, Dict

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.exceptions import TikTokYtDlpDownloaderError
from saver_backend.services.downloaders.ydl_source import YtDlpController

if TYPE_CHECKING:
    from saver_backend.db.dao.video_cache_dao import VideoCacheDAO
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class TikTokYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from TikTok through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.TIKTOK

    def __init__(
        self,
        resolution: Resolution,
        telegram_bot_controller: "TelegramBotController",
        telegram_id: int,
        video_cache_dao: "VideoCacheDAO",
        message_id: int | None = None,
    ) -> None:
        super().__init__(
            resolution,
            telegram_bot_controller,
            telegram_id,
            video_cache_dao,
            message_id,
        )
        self._yt_dlp.params["format"] = "bv*+ba/best"

    async def get_video_info(self, url: str) -> Dict[str, Any] | None:
        """Override to raise a specific error for slideshows (which return no info)."""
        info = await super()._get_video_info(url)
        if not info:
            raise TikTokYtDlpDownloaderError
        return info

    async def _execute_download(self, info: dict[str, Any]) -> None:
        """
        Execute the download with specific error handling for TikTok.

        Catches TikTokYtDlpDownloaderError which is often caused by slideshows
        and informs the user gracefully.

        :param info: The pre-fetched video information dictionary.
        """
        try:
            await super()._execute_download(info)
        except TikTokYtDlpDownloaderError:
            logging.warning(
                "Could not get info for TikTok URL %s. It might be a slideshow.",
                self._resolution.url,
            )
            if self._message_id:
                await self._telegram_bot_controller.bot.delete_message(
                    chat_id=self._telegram_id,
                    message_id=self._message_id,
                )
            await self._telegram_bot_controller.send_tiktok_error_downloading(
                telegram_id=self._telegram_id,
            )
