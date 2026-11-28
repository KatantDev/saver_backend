import asyncio
import logging
import re
from typing import Any, ClassVar, Dict

from yt_dlp import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.instagram_session import YDL_COOKIES_FILE
from saver_backend.services.downloaders.schema import PhotoDTO, VideoDTO
from saver_backend.services.downloaders.ydl_source import (
    RETRY_PAUSE_SECONDS,
    YtDlpController,
)

# Fields of a post yt-dlp expects on every item it processes
POST_FIELDS = (
    "extractor",
    "extractor_key",
    "webpage_url",
    "original_url",
    "webpage_url_basename",
    "webpage_url_domain",
)
# Size mark in the address of a scaled-down Instagram picture (s640x640, p1080x1080)
SCALED_PICTURE_RE = re.compile(r"_[sp]\d+x\d+")

# What Instagram answers for a deleted, private or never existing post
CONTENT_UNAVAILABLE_HINTS = (
    "empty media response",
    "requested content is not available",
    "content isn't available",
    "content is not available",
    "this post is unavailable",
    "http error 404",
    # a private account: only its followers see the post
    "only available for registered users who follow this account",
)


def is_content_unavailable(error: DownloadError) -> bool:
    """
    Whether yt-dlp failed because the post is gone or closed, not a network error.

    :param error: Error raised by yt-dlp.
    :return: True if the user should be told the post is private or deleted.
    """
    message = str(error.msg or error).lower()
    return any(hint in message for hint in CONTENT_UNAVAILABLE_HINTS)


class InstagramYdlController(YtDlpController):
    """Asynchronous controller for downloading videos from Instagram through yt-dlp."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.INSTAGRAM_YDL
    COOKIES: ClassVar[bool] = True
    DIRECT_URL_DOWNLOAD: ClassVar[bool] = True

    def __init__(self, *args: Any, use_session: bool = True, **kwargs: Any) -> None:
        # Read before the parent picks the cookie file
        self._use_session = use_session
        super().__init__(*args, **kwargs)
        self._yt_dlp.params["format"] = "best"

    def _set_cookies(self, force_set: bool = False) -> None:
        # The service account session, when there is one, beats a random pick
        # among older cookie files in the same folder. A request that does not
        # need the account leaves it alone: Instagram locks accounts that read
        # a thousand posts a day from a server address.
        if self._use_session and YDL_COOKIES_FILE.exists():
            self._base_options["cookiefile"] = str(YDL_COOKIES_FILE)
            logging.info("Using Instagram service account cookies %s", YDL_COOKIES_FILE)
            return
        super()._set_cookies(force_set)

    async def get_video_info(self, url: str) -> Dict[str, Any] | None:
        """
        Get video information without downloading.

        :param url: URL of the video.
        :return: Dictionary with video information.
        """
        try:
            return await super().get_video_info(url)
        except DownloadError as error:
            if not is_content_unavailable(error) or self._inline_query_id:
                raise
            # A deleted or private post: say so instead of a generic error
            logging.info("Instagram content unavailable: %s (%s)", url, error.msg)
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return None

    async def fetch_media(self, url: str) -> list[PhotoDTO | VideoDTO]:
        """
        Read a post and return its videos and photos as links, nothing is saved.

        yt-dlp refuses a post without video («There is no video in this post»,
        «No video formats found»), although Instagram's answer it reads has the
        photos too: the full-size picture is the last thumbnail of each item.

        :param url: Link to a post or a reel.
        :return: Items of the post in order, with direct links.
        :raises DownloadError: Instagram refused the request on every route.
        """
        attempts = self.RETRIES_ON_ERROR + 2
        for attempt in range(attempts):
            try:
                info = await asyncio.to_thread(self._extract_unprocessed, url)
                return await asyncio.to_thread(self._media_from_info, info, url)
            except DownloadError as error:
                if attempt == attempts - 1 or is_content_unavailable(error):
                    raise
                logging.warning(
                    "[%s] reading %s failed, trying another proxy: %s",
                    self.SOURCE.value,
                    url,
                    error,
                )
                self._switch_proxy_for_retry()
                await asyncio.sleep(RETRY_PAUSE_SECONDS)
        return []

    def _extract_unprocessed(self, url: str) -> dict[str, Any]:
        params = {**self._yt_dlp.params, "ignore_no_formats_error": True}
        self._yt_dlp = self._create_yt_dlp(params)
        info = self._yt_dlp.extract_info(url=url, download=False, process=False)
        return info or {}

    def _media_from_info(
        self,
        info: dict[str, Any],
        url: str,
    ) -> list[PhotoDTO | VideoDTO]:
        entries = info.get("entries")
        items = list(entries) if entries is not None else [info]
        # Text of the post; items of a carousel do not carry it
        description = info.get("description") or None
        media: list[PhotoDTO | VideoDTO] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("formats"):
                # items of a carousel come without the fields of the post
                shared = {key: info[key] for key in POST_FIELDS if key in info}
                video = self._video_from_item({**shared, **item}, url)
                video.description = description
                media.append(video)
                continue
            picture = full_size_picture(item.get("thumbnails") or [])
            if picture:
                media.append(
                    PhotoDTO(
                        url=url,
                        source_id=str(item.get("id") or info.get("id") or ""),
                        media_url=picture,
                        description=description,
                    ),
                )
        return media

    def _video_from_item(self, item: dict[str, Any], url: str) -> VideoDTO:
        # Picks the format the usual way (best file with sound), no network
        processed = self._yt_dlp.process_ie_result(dict(item), download=False)
        video = VideoDTO.from_yt_dlp(
            info=processed,
            extract_direct_links=True,
            quality="best",
        )
        video.url = url
        return video


def full_size_picture(thumbnails: list[dict[str, Any]]) -> str | None:
    """
    Address of the original picture among the thumbnails of a post item.

    Instagram lists the scaled copies first and the original last; the copies
    carry their size in the address.

    :param thumbnails: Thumbnails of one item as yt-dlp returns them.
    :return: Address of the biggest picture, None when there are none.
    """
    urls = [str(item["url"]) for item in thumbnails if item.get("url")]
    if not urls:
        return None
    originals = [link for link in urls if not SCALED_PICTURE_RE.search(link)]
    return (originals or urls)[-1]
