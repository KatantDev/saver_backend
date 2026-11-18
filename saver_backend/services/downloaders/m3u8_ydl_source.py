from typing import Any, ClassVar
from urllib.parse import urlparse

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController

# Best video with the best separate audio, or the best stream that has both
M3U8_DEFAULT_FORMAT = "bv*+ba/b"


class M3U8YdlController(YtDlpController):
    """Asynchronous controller for downloading raw HLS (m3u8) streams via yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.M3U8_YDL
    COOKIES: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with yt-dlp parameters for HLS streams."""
        super().__init__(*args, **kwargs)

        params: dict[str, Any] = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        if not self._selected_format_id:
            # Many playlists keep video and audio in separate streams: «best»
            # (one stream with both) does not exist there and yt-dlp fails
            params["format"] = M3U8_DEFAULT_FORMAT
            params["merge_output_format"] = "mp4"
        self._yt_dlp.params.update(params)
        if "format" in params:
            self._yt_dlp.format_selector = self._yt_dlp.build_format_selector(
                format_spec=params["format"],
            )

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
            # Same name as yt-dlp writes (outtmpl «%(id)s.<token>.%(ext)s»);
            # without the token the finished file was never found («Error»)
            file_path=self._download_directory
            / f"{url_path}.{self._download_token}.{ext}",
            extract_direct_links=self.DIRECT_URL_DOWNLOAD,
            quality=self._selected_format_id or "best",
        )

        return info_dict
