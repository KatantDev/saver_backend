from typing import Any, ClassVar
from urllib.parse import urlparse

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController


class M3U8YdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Rutube through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.M3U8_YDL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for Rutube."""
        super().__init__(*args, **kwargs)

        params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(params)

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info from m3u8 url.

        :param url: The url to get video info from.
        :return: The video info or None.
        """
        info_dict = await super().get_video_info(url=url)
        if info_dict is None or self._video is None:
            return None

        url_path = (
            urlparse(self._resolution.url)
            .path[:200]
            .replace("/", "-")
            .replace(".", "-")
        )
        ext = info_dict.get("ext")

        info_dict.update(
            {
                "id": url_path,
                "title": url_path,
                "fulltitle": url_path,
            },
        )
        self._video = VideoDTO.from_yt_dlp(
            info=info_dict,
            file_path=self._download_directory / f"{url_path}.{ext}",
            extract_direct_links=self.DIRECT_URL_DOWNLOAD,
            quality=self._selected_format_id or "best",
        )

        return info_dict
