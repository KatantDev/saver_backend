import logging
import uuid
from pathlib import Path
from typing import Any, ClassVar

import httpx

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import PhotoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController

PICTURE_TIMEOUT = 30


class PinterestYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Pinterest through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.PINTEREST_YDL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for Pinterest."""
        super().__init__(*args, **kwargs)

        pinterest_params = {
            # Old pins have one file with picture and sound and no separate
            # streams: without «/best» they failed with «Requested format is
            # not available»
            "format": "bestvideo+bestaudio/best",
            # A pin with a picture has no video formats; it is sent as a photo
            "ignore_no_formats_error": True,
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(pinterest_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=pinterest_params["format"],
        )

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info; a pin with a picture is sent right away as a photo.

        Most pins are pictures. yt-dlp answered «No video formats found» for
        them and the user got the general error (14 in three days).

        :param url: URL of the pin.
        :return: Dictionary with video information, None when the pin was a
            picture (already sent) or could not be read.
        """
        info = await super().get_video_info(url)
        if info and not info.get("formats"):
            await self._send_picture(info)
            return None
        return info

    async def _send_picture(self, info: dict[str, Any]) -> None:
        """
        Download the original picture of a pin and send it.

        :param info: The dictionary from yt_dlp.extract_info.
        """
        thumbnails = [item for item in info.get("thumbnails") or [] if item.get("url")]
        if not thumbnails:
            logging.error(
                "Pinterest pin has neither video nor picture: %s", info.get("id")
            )
            await self._send_error_message()
            return
        # yt-dlp sorts thumbnails from the smallest to the original
        picture_url = str(thumbnails[-1]["url"])
        extension = Path(picture_url.split("?", maxsplit=1)[0]).suffix.lower() or ".jpg"
        path = self._download_directory / (
            f"{info.get('id')}.{uuid.uuid4().hex[:6]}{extension}"
        )
        try:
            await self._download_picture(picture_url, path)
        except httpx.HTTPError as e:
            logging.error(
                "Pinterest picture of %s was not downloaded: %s",
                info.get("id"),
                type(e).__name__,
            )
            await self._send_error_message()
            return
        photo = PhotoDTO(
            url=self._resolution.url,
            source_id=str(info.get("id") or ""),
            path=path,
        )
        try:
            send = (
                self._telegram_bot_controller.send_finish_downloading_gif
                if extension == ".gif"
                else self._telegram_bot_controller.send_finish_downloading_photo
            )
            await send(
                photo, telegram_id=self._telegram_id, message_id=self._message_id
            )
            self._message_id = None
            await self._create_history_entry()
        finally:
            path.unlink(missing_ok=True)

    async def _download_picture(self, url: str, path: Path) -> None:
        last_error: httpx.HTTPError | None = None
        # Through the proxy the page was read with, then directly
        for proxy in dict.fromkeys([self._proxy or None, None]):
            try:
                async with httpx.AsyncClient(
                    proxy=proxy,
                    timeout=PICTURE_TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    path.write_bytes(response.content)
                    return
            except httpx.HTTPError as e:
                last_error = e
        if last_error:
            raise last_error
