import logging
from typing import Any, ClassVar

from httpx import AsyncClient, HTTPError
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.adult_filter import (
    is_gay_info,
    is_gay_page,
    needs_page_check,
)
from saver_backend.services.downloaders.ydl_source import YtDlpController

PAGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


class AdultYdlController(YtDlpController):
    """Generic controller for downloading videos from adult sites via yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.ADULT_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with optimal yt-dlp parameters for video sites."""
        super().__init__(*args, **kwargs)

        adult_params = {
            "format": "best",
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(adult_params)
        self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
            format_spec=adult_params["format"],
        )

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Read the video info and refuse gay-category videos.

        The check runs before the cache lookup and the download, so such a video
        is never sent, even if it was cached before.

        :param url: URL of the video.
        :return: Info of the video, None when it is refused or not found.
        """
        info = await super().get_video_info(url)
        if info is None:
            return None
        if is_gay_info(info) or await self._page_is_gay(
            str(info.get("webpage_url") or url),
        ):
            logging.info("Refused a gay-category video: %s", url)
            self._video = None
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_blocked(
                telegram_id=self._telegram_id,
            )
            return None
        return info

    async def _page_is_gay(self, url: str) -> bool:
        """
        Read the category from the video page on sites that keep it there.

        :param url: URL of the video page.
        :return: True for a gay-category video.
        :raises DownloadError: The page could not be read, so the video is not
            sent unchecked; the caller retries through another proxy.
        """
        if not needs_page_check(url):
            return False
        try:
            async with AsyncClient(proxy=self._proxy or None, timeout=20) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": PAGE_USER_AGENT},
                    follow_redirects=True,
                )
                response.raise_for_status()
        except HTTPError as e:
            raise DownloadError(f"Could not check the video page: {e}") from e
        return is_gay_page(response.text)
