from typing import Any, ClassVar

from yt_dlp import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController
from saver_backend.services.downloaders.youtube_video_ydl_source import (
    YOUTUBE_H264_FORMAT,
    YOUTUBE_PLAYER_CLIENTS,
    YouTubeClientRotationMixin,
    youtube_extractor_args,
)


class YouTubeShortsYdlController(YouTubeClientRotationMixin, YtDlpController):
    """Asynchronous controller for downloading videos from YT Shorts through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YOUTUBE_SHORTS_YDL
    # Prod drops ~1 in 5 YouTube downloads once while a retry passes (task 24)
    RETRIES_ON_ERROR: ClassVar[int] = 1
    # A blocked proxy gives 403 while the server itself can reach YouTube
    DIRECT_FALLBACK: ClassVar[bool] = True
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with custom yt-dlp parameters for YouTube."""
        super().__init__(*args, **kwargs)

        youtube_params = {
            "format": YOUTUBE_H264_FORMAT,
            "merge_output_format": "mp4",
            "extractor_args": youtube_extractor_args(YOUTUBE_PLAYER_CLIENTS[0]),
            "remote_components": ["ejs:github"],
        }
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=youtube_params["format"]
        )
        self._yt_dlp.params.update(youtube_params)

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info, with specific handling for private/restricted videos.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        try:
            return await super().get_video_info(url)
        except DownloadError as e:
            if "Video unavailable" in str(e):
                await self.delete_processing_message()
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
                return None
            raise
