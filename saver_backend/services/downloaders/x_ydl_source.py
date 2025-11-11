import logging
from typing import Any, ClassVar
from urllib.parse import urlparse, urlunparse

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController


class XYdlController(YtDlpController):
    """Controller for downloading videos from X/Twitter with a text fallback."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.X_YDL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with specific yt-dlp parameters for X."""
        super().__init__(*args, **kwargs)

        x_params = {
            "format": "bestvideo+bestaudio/best",
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(x_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=x_params["format"],
        )

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Attempt to get video info from X.

        If any DownloadError occurs, trigger the fallback link mechanism and
        return None to stop the download process.
        """
        try:
            return await super().get_video_info(url)
        except DownloadError as e:
            logging.warning(
                "Could not download video from X URL %s: %s. Sending fallback link.",
                self._resolution.url,
                e.msg,
            )
            await self._send_fallback_link()
            return None

    async def _send_fallback_link(self) -> None:
        """Replace the domain with 'fixupx.com' and send it to the user."""
        parsed_url = urlparse(self._resolution.url)
        # Replace netloc (domain) and keep everything else
        fixed_url = urlunparse(
            (
                parsed_url.scheme,
                "fixupx.com",
                parsed_url.path,
                parsed_url.params,
                parsed_url.query,
                parsed_url.fragment,
            ),
        )
        await self._telegram_bot_controller.send_x_fallback_message(
            telegram_id=self._telegram_id,
            fixed_url=fixed_url,
        )

        # Clean up processing message and create history entry
        await self.delete_processing_message()
        await self._create_history_entry()
