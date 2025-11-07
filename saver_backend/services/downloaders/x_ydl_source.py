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

    async def download_video(self) -> None:
        """
        Attempt to download a video from X/Twitter.

        If the download fails (e.g., no video in the tweet), it sends a
        fallback message with a 'fixupx.com' link to the user.
        """
        try:
            # First, attempt the standard download process from the parent class
            await super().download_video()
        except DownloadError as e:
            # This is the special fallback logic for X
            logging.warning(
                "Could not download video from X URL %s: %s. Sending fallback link.",
                self._resolution.url,
                e.msg,
            )
            await self.delete_processing_message()
            await self._send_fallback_link()
        except Exception:
            # Catch any other unexpected errors and send the fallback
            logging.exception(
                "An unexpected error occurred while downloading from X URL %s. "
                "Sending fallback link.",
                self._resolution.url,
            )
            await self.delete_processing_message()
            await self._send_fallback_link()

    async def _send_fallback_link(self) -> None:
        """Replace the domain with 'fixupx.com' and send it to the user."""
        try:
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
        except Exception:
            logging.exception("Failed to send fallback link for X.")
            # If even sending the fallback fails, send a generic error
            await self._send_error_message(with_delete=False)
