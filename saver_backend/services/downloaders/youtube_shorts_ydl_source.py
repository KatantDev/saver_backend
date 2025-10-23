from typing import TYPE_CHECKING, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.ydl_source import YtDlpController

if TYPE_CHECKING:
    from saver_backend.db.dao.video_cache_dao import VideoCacheDAO
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class YouTubeShortsYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from YT Shorts through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YOUTUBE_SHORTS_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(
        self,
        resolution: Resolution,
        telegram_bot_controller: "TelegramBotController",
        telegram_id: int,
        video_cache_dao: "VideoCacheDAO",
        message_id: int | None = None,
    ) -> None:
        """Initialize the controller with custom yt-dlp parameters for YouTube."""
        super().__init__(
            resolution,
            telegram_bot_controller,
            telegram_id,
            video_cache_dao,
            message_id,
        )

        youtube_params = {
            "format": "bestvideo[ext=mp4][height<=1080]+bestaudio/best[ext=mp4]",
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
            "extractor_args": {
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://saver_backend-bgutil:4416"],
                },
            },
        }
        self._yt_dlp.params.update(youtube_params)
