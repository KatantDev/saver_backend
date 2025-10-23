from typing import TYPE_CHECKING, Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController

if TYPE_CHECKING:
    from saver_backend.db.dao.video_cache_dao import VideoCacheDAO


class YouTubeShortsYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from YT Shorts through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YOUTUBE_SHORTS_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(
        self,
        *args: Any,
        video_cache_dao: "VideoCacheDAO",
        **kwargs: Any,
    ) -> None:
        """Initialize the controller with custom yt-dlp parameters for YouTube."""
        super().__init__(*args, video_cache_dao=video_cache_dao, **kwargs)

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
