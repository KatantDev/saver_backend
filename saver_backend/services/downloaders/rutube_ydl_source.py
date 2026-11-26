import logging
from typing import Any, ClassVar
from urllib.parse import urlparse

from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.downloaders.ydl_source import YtDlpController

# Rutube answers 403 to the request for a video its author deleted and for a
# private one opened without its key: nothing to retry, the user is told so.
RUTUBE_GONE_HINT = "Unable to download video JSON: HTTP Error 403"
# The list of qualities comes from a balancer of its own. When it does not
# answer, yt-dlp only warns and then says the video has no formats (prod
# 24.11: four requests in an hour, each after 41 seconds of waiting).
NO_FORMATS_HINT = "No video formats found"
INFO_ATTEMPTS = 3


def _host(format_info: dict[str, Any]) -> str:
    return urlparse(str(format_info.get("url") or "")).hostname or ""


def mirrors_of(info_dict: dict[str, Any]) -> list[str]:
    """
    Find the same quality on the other file servers of Rutube.

    Every quality is listed twice, once per file server, and yt-dlp takes the
    last one. On prod on 24.11 that server (river-1.rutube.ru) did not answer
    for an hour and three downloads failed while the other server was there.

    :param info_dict: The dictionary from yt_dlp.extract_info, format picked.
    :return: Ids of the formats to try instead, the closest first.
    """
    formats = info_dict.get("formats") or []
    picked_id = str(info_dict.get("format_id") or "")
    picked = next((f for f in formats if f.get("format_id") == picked_id), None)
    if picked is None or not picked.get("height"):
        return []
    balancer = picked_id.split("-", maxsplit=1)[0]
    others = [
        str(f["format_id"])
        for f in formats
        if f.get("height") == picked["height"]
        and _host(f) != _host(picked)
        and f.get("format_id")
    ]
    return sorted(others, key=lambda format_id: not format_id.startswith(balancer))


class RutubeYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Rutube through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.RUTUBE_YDL
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.RU
    COOKIES: ClassVar[bool] = False
    # A file server or a proxy that does not answer: two more tries, each
    # through the next proxy and from the other file server
    RETRIES_ON_ERROR: ClassVar[int] = 2

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with standard yt-dlp parameters for Rutube."""
        super().__init__(*args, **kwargs)
        # The picked quality on the other file servers, for the retry
        self._mirrors: list[str] = []

        rutube_params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
        }
        self._yt_dlp.params.update(rutube_params)

    def _prepare_info(self, info_dict: dict[str, Any]) -> dict[str, Any]:
        self._mirrors = mirrors_of(info_dict)
        return info_dict

    def _switch_proxy_for_retry(self) -> None:
        """Take the next proxy and, after a download, the other file server."""
        YtDlpController._switch_proxy_for_retry(self)  # noqa: SLF001
        if not self._mirrors:
            return
        chain = "/".join([*self._mirrors, self._selected_format_id or "best"])
        logging.info(
            "[%s] the next try of %s takes the formats %s",
            self.SOURCE.value,
            self._resolution.url,
            chain,
        )
        self._yt_dlp = self._create_yt_dlp({**self._yt_dlp.params, "format": chain})

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info, asking again when the list of qualities did not come.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        for attempt in range(1, INFO_ATTEMPTS + 1):
            try:
                return await YtDlpController.get_video_info(self, url)
            except DownloadError as e:
                if RUTUBE_GONE_HINT in str(e):
                    logging.info("Rutube video is deleted or private: %s (%s)", url, e)
                    await self.delete_processing_message()
                    await self._telegram_bot_controller.send_content_not_found_error(
                        telegram_id=self._telegram_id,
                    )
                    return None
                if NO_FORMATS_HINT not in str(e) or attempt == INFO_ATTEMPTS:
                    raise
                logging.warning(
                    "[%s] no list of qualities for %s, attempt %s of %s",
                    self.SOURCE.value,
                    url,
                    attempt,
                    INFO_ATTEMPTS,
                )
                self._switch_proxy_for_retry()
        return None
