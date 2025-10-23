from typing import TYPE_CHECKING, Any, ClassVar, Dict

from yt_dlp import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.ydl_source import YtDlpController

if TYPE_CHECKING:
    from saver_backend.db.dao.video_cache_dao import VideoCacheDAO
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class InstagramYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Instagram through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.INSTAGRAM_YDL
    COOKIES: ClassVar[bool] = True

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
        self._yt_dlp.params["format"] = "best"

    async def get_video_info(self, url: str) -> Dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information.
        """
        try:
            return await super()._get_video_info(url)
        except DownloadError as error:
            if "No video formats found" in error.msg:
                raise
            raise
