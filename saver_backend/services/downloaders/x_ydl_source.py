import asyncio
import logging
import re
from typing import Any, ClassVar
from urllib.parse import urlparse, urlunparse

from httpx import AsyncClient, HTTPError
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController

# X serves up to 4K (160 MB for 86 s), far more than a phone needs. The cap is
# on the smaller side of the frame, so vertical videos also stay at 1080x1920.
X_MAX_RESOLUTION = 1080

# Prod 21-23.11: 143 of 347 X requests ended with the fixupx link instead of a
# video, because yt-dlp answered «No video could be found in this tweet» (X hides
# sensitive and some other tweets from guests). FxTwitter returns a direct mp4
# link for the same tweets.
FXTWITTER_API = "https://api.fxtwitter.com/i/status/{tweet_id}"
FXTWITTER_USER_AGENT = "Mozilla/5.0 (compatible; saver-bot)"
TWEET_ID_RE = re.compile(r"/status/(\d+)")
VIDEO_INDEX_RE = re.compile(r"/video/(\d+)")
MAX_TITLE_LENGTH = 200


def tweet_id_of(url: str) -> str | None:
    """Tweet id from an X link."""
    match = TWEET_ID_RE.search(url)
    return match.group(1) if match else None


def video_index_of(url: str) -> int:
    """Zero-based index of the video an X link points at (…/video/2 is 1)."""
    match = VIDEO_INDEX_RE.search(url)
    return max(int(match.group(1)) - 1, 0) if match else 0


def fxtwitter_info(payload: dict[str, Any], url: str) -> dict[str, Any] | None:
    """
    Build yt-dlp style info of a tweet's video from FxTwitter's answer.

    :param payload: JSON returned by the FxTwitter API.
    :param url: The X link the user sent.
    :return: Info for yt-dlp, None when the tweet has no video.
    """
    tweet = payload.get("tweet") or {}
    videos = (tweet.get("media") or {}).get("videos") or []
    tweet_id = tweet_id_of(url)
    if not videos or not tweet_id:
        return None
    index = video_index_of(url)
    video = videos[index] if index < len(videos) else videos[0]
    file_url = video.get("url")
    if not file_url:
        return None
    author = tweet.get("author") or {}
    title = (tweet.get("text") or "").strip() or author.get("name") or tweet_id
    width, height = video.get("width"), video.get("height")
    return {
        "id": tweet_id if index == 0 else f"{tweet_id}_{index + 1}",
        "title": title[:MAX_TITLE_LENGTH],
        "url": file_url,
        "ext": "mp4",
        "vcodec": "h264",
        "acodec": "aac",
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else None,
        "duration": video.get("duration"),
        "thumbnail": video.get("thumbnail_url"),
        "webpage_url": url,
        "uploader": author.get("name"),
        "uploader_id": author.get("screen_name"),
        "extractor": "twitter",
        "extractor_key": "Twitter",
    }


class XYdlController(YtDlpController):
    """Controller for downloading videos from X/Twitter with a text fallback."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.X_YDL
    COOKIES: ClassVar[bool] = False
    MAX_RESOLUTION: ClassVar[int | None] = X_MAX_RESOLUTION

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the controller with specific yt-dlp parameters for X."""
        super().__init__(*args, **kwargs)

        x_params = {
            "format": "bestvideo+bestaudio/best",
            # Best formats up to 1080p come first; a bigger one only if nothing else
            "format_sort": [f"res:{X_MAX_RESOLUTION}"],
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
            info = await self._info_from_fxtwitter()
            if info is not None:
                return info
            await self._send_fallback_link()
            return None

    async def _info_from_fxtwitter(self) -> dict[str, Any] | None:
        """
        Read the tweet's video from FxTwitter when yt-dlp could not.

        :return: Info ready for the download, None when FxTwitter has no video.
        """
        tweet_id = tweet_id_of(self._resolution.url)
        if not tweet_id:
            return None
        try:
            async with AsyncClient(proxy=self._proxy or None, timeout=20) as client:
                response = await client.get(
                    FXTWITTER_API.format(tweet_id=tweet_id),
                    headers={"User-Agent": FXTWITTER_USER_AGENT},
                    follow_redirects=True,
                )
                response.raise_for_status()
                payload = response.json()
        except (HTTPError, ValueError) as e:
            logging.warning("FxTwitter did not answer for %s: %s", tweet_id, e)
            return None
        raw_info = fxtwitter_info(payload, self._resolution.url)
        if raw_info is None:
            return None
        try:
            info: dict[str, Any] = await asyncio.to_thread(
                self._yt_dlp.process_ie_result,
                raw_info,
                download=False,
            )
        except DownloadError as e:
            logging.warning("FxTwitter video of %s was not accepted: %s", tweet_id, e)
            return None
        logging.info("X video %s is taken from FxTwitter", tweet_id)
        self._video = VideoDTO.from_yt_dlp(
            info=info,
            file_path=self._download_directory
            / f"{info['id']}.{self._download_token}.{info['ext']}",
            extract_direct_links=self.DIRECT_URL_DOWNLOAD,
            quality=self._selected_format_id or "best",
        )
        return info

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
