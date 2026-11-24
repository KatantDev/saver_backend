import asyncio
import logging
from typing import Any, ClassVar

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController

# ok.ru answers about every fifth request with a page that has no player in it;
# the next request for the same video works (14 of 72 on prod on 24.11).
PLAYER_ERROR = "Unable to extract player"
PLAYER_ATTEMPTS = 3
PLAYER_RETRY_PAUSE = 1.0

# Plain https files of ok.ru: yt-dlp gives them a name and no size of the frame.
# About every fifth answer has only these and no streams, and the bot said
# «no formats» (71 times for one user on prod on 23.11).
PLAIN_FILE_HEIGHTS = {
    "mobile": 144,
    "lowest": 240,
    "low": 360,
    "sd": 480,
    "hd": 720,
    "full": 1080,
    "quad": 1440,
    "ultra": 2160,
}


class OkYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from ok.ru through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.OK_YDL
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.RU
    COOKIES: ClassVar[bool] = False
    # At download the answer may again come without the player or without the
    # stream the user picked from the menu: two more tries through other proxies
    RETRIES_ON_ERROR: ClassVar[int] = 2

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for ok.ru."""
        super().__init__(*args, **kwargs)

        ok_params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(ok_params)

    def _prepare_info(self, info_dict: dict[str, Any]) -> dict[str, Any]:
        """
        Give the plain https files their height so they count as qualities.

        :param info_dict: The dictionary from yt_dlp.extract_info.
        :return: The same dictionary with heights filled in.
        """
        for format_info in info_dict.get("formats") or []:
            height = PLAIN_FILE_HEIGHTS.get(str(format_info.get("format_id")))
            if height and not format_info.get("height"):
                format_info["height"] = height
        return info_dict

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info, asking again when ok.ru returns a page without the player.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        for attempt in range(1, PLAYER_ATTEMPTS + 1):
            try:
                return await YtDlpController.get_video_info(self, url)
            except DownloadError as e:
                if PLAYER_ERROR not in str(e) or attempt == PLAYER_ATTEMPTS:
                    raise
                logging.warning(
                    "[%s] no player in the page of %s, attempt %s of %s",
                    self.SOURCE.value,
                    url,
                    attempt,
                    PLAYER_ATTEMPTS,
                )
                self._switch_proxy_for_retry()
                await asyncio.sleep(PLAYER_RETRY_PAUSE)
        return None
