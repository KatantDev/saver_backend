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
            "downloader": "aria2c",
            "external_downloader_args": {
                "aria2c": [
                    "-x",
                    "16",
                    "-s",
                    "16",
                    "-k",
                    "1M",
                    "--timeout=60",  # Таймаут для соединения
                    "--max-tries=10",  # Максимальное количество попыток
                    "--retry-wait=5",  # Ждать между попытками
                ],
            },
            "extractor_args": {
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://saver_backend-bgutil:4416"],
                },
            },
            "js_runtimes": {
                "node": {
                    "enabled": True,
                },
            },  # Опция для использования Node.js JavaScript runtime
            "verbose": True,  # Подробный лог
            "debug": True,
        }
        self._yt_dlp.params.update(youtube_params)
