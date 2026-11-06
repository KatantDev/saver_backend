import logging
from typing import TYPE_CHECKING, Any, ClassVar, Dict

from yt_dlp import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.instagram_session import YDL_COOKIES_FILE
from saver_backend.services.downloaders.ydl_source import YtDlpController

if TYPE_CHECKING:
    pass


class InstagramYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Instagram through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.INSTAGRAM_YDL
    COOKIES: ClassVar[bool] = True
    DIRECT_URL_DOWNLOAD: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._yt_dlp.params["format"] = "best"

    def _set_cookies(self, force_set: bool = False) -> None:
        # The service account session, when there is one, beats a random pick
        # among older cookie files in the same folder
        if YDL_COOKIES_FILE.exists():
            self._base_options["cookiefile"] = str(YDL_COOKIES_FILE)
            logging.info("Using Instagram service account cookies %s", YDL_COOKIES_FILE)
            return
        super()._set_cookies(force_set)

    async def get_video_info(self, url: str) -> Dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information.
        """
        try:
            return await super().get_video_info(url)
        except DownloadError as error:
            if "No video formats found" in error.msg:
                raise
            raise
