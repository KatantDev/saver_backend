import logging
import secrets
from pathlib import Path
from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class YouTubeVideoYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from YouTube through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YOUTUBE_VIDEO_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for YouTube."""
        super().__init__(*args, **kwargs)

        youtube_params = {
            "format": "bestvideo[ext=mp4][height<=1080]+bestaudio/best[ext=mp4]",
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(youtube_params)

    def _set_cookies(self) -> None:
        """
        Override to use cookies from the YouTube Shorts directory.

        This ensures that both shorts and regular videos use the same
        authenticated session to bypass age restrictions.
        """
        if not self.COOKIES:
            return

        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        cookie_dir = base_dir / "cookies" / SourceEnum.YOUTUBE_SHORTS_YDL.value
        cookie_files = list(cookie_dir.glob("cookies*.txt"))
        if not cookie_files:
            logging.error(
                "Cookies enabled for %s, but no files found in %s",
                self.SOURCE,
                cookie_dir,
            )
            return

        cookie_file = secrets.choice(cookie_files).resolve()
        self._base_options["cookiefile"] = str(cookie_file)
        logging.info(
            "Using YouTube Shorts cookie file %s for %s",
            cookie_file,
            self.SOURCE,
        )
