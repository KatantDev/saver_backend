from typing import Any, ClassVar

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController

# The smaller side of the picture is capped: a stream recording may be 4K
GENERIC_MAX_RESOLUTION = 1080


class GenericYdlController(YtDlpController):
    """
    Sites yt-dlp reads without anything special on our side.

    Twitch clips and recordings and medal.tv clips were answered «not
    supported» although the same download as for other sites works for them.
    A site is added here through the detector's list, not by default: yt-dlp
    knows a thousand sites and most of them were never checked with the bot.
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.GENERIC_YDL
    COOKIES: ClassVar[bool] = False
    RETRIES_ON_ERROR: ClassVar[int] = 1
    DIRECT_FALLBACK: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with a format that plays in Telegram."""
        super().__init__(*args, **kwargs)

        params = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "format_sort": [f"res:{GENERIC_MAX_RESOLUTION}"],
            "merge_output_format": "mp4",
        }
        self._yt_dlp.params.update(params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=params["format"],
        )
