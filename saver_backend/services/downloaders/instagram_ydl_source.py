import logging
from typing import TYPE_CHECKING, Any, ClassVar, Dict

from yt_dlp import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.instagram_session import YDL_COOKIES_FILE
from saver_backend.services.downloaders.ydl_source import YtDlpController

if TYPE_CHECKING:
    pass


# What Instagram answers for a deleted, private or never existing post
CONTENT_UNAVAILABLE_HINTS = (
    "empty media response",
    "requested content is not available",
    "content isn't available",
    "content is not available",
    "this post is unavailable",
    "http error 404",
)


def is_content_unavailable(error: DownloadError) -> bool:
    """
    Whether yt-dlp failed because the post is gone or closed, not a network error.

    :param error: Error raised by yt-dlp.
    :return: True if the user should be told the post is private or deleted.
    """
    message = str(error.msg or error).lower()
    return any(hint in message for hint in CONTENT_UNAVAILABLE_HINTS)


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
            if not is_content_unavailable(error) or self._inline_query_id:
                raise
            # A deleted or private post: say so instead of a generic error
            logging.info("Instagram content unavailable: %s (%s)", url, error.msg)
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return None
