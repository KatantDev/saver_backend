import asyncio
import logging
import re
import secrets
from pathlib import Path
from typing import Any

import httpx
from yt_dlp.utils import DownloadError

from saver_backend.services.consts import MAX_FILE_SIZE_BYTES
from saver_backend.services.downloaders.ydl_source import is_cookie_file

# One folder for every VK source: cookies/vk/cookies*.txt
VK_COOKIES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "cookies" / "vk"
# The video opens only for a logged-in VK user
SIGN_IN_TEXT = "only available to signed-in users"
# Answers that mean the video is closed or gone, whoever asks
VK_GONE_HINTS = (
    "access restricted",
    "creator has been blocked",
    "has been deleted",
    "only available to followers",
)
TAG_RE = re.compile(r"<[^>]+>")

# Plain https files come without a size. Short and small ones cannot reach
# Telegram's limit, so only long videos in high quality are measured.
SIZE_CHECK_MIN_DURATION = 20 * 60
SIZE_CHECK_MIN_HEIGHT = 720
SIZE_CHECK_TIMEOUT = 6


def is_plain_file(format_info: dict[str, Any]) -> bool:
    """Whether the format is one plain file, not a stream."""
    return str(format_info.get("protocol") or "") in ("https", "http")


async def fill_plain_file_sizes(
    info_dict: dict[str, Any],
    proxy: str | None,
) -> dict[str, Any]:
    """
    Ask the size of big plain https files so the menu can leave out those over 2 GB.

    VK and ok.ru list such files without a size; the bot offered them, downloaded
    for minutes and only then said the file is too big (about 27 times a day).

    :param info_dict: The dictionary from yt_dlp.extract_info.
    :param proxy: Proxy the page was read through.
    :return: The same dictionary with sizes filled in where the server gave them.
    """
    if (info_dict.get("duration") or 0) < SIZE_CHECK_MIN_DURATION:
        return info_dict
    unknown = [
        fmt
        for fmt in info_dict.get("formats") or []
        if is_plain_file(fmt)
        and fmt.get("url")
        and not fmt.get("filesize")
        and (fmt.get("height") or 0) >= SIZE_CHECK_MIN_HEIGHT
    ]
    if not unknown:
        return info_dict

    async with httpx.AsyncClient(
        proxy=proxy or None,
        timeout=SIZE_CHECK_TIMEOUT,
        follow_redirects=True,
    ) as client:

        async def measure(fmt: dict[str, Any]) -> None:
            try:
                response = await client.head(
                    fmt["url"],
                    headers=fmt.get("http_headers") or {},
                )
                size = int(response.headers.get("content-length") or 0)
            except httpx.HTTPError, ValueError:
                return
            if response.status_code < httpx.codes.BAD_REQUEST and size:
                fmt["filesize"] = size

        await asyncio.gather(*(measure(fmt) for fmt in unknown))

    too_big = [
        fmt.get("format_id")
        for fmt in unknown
        if (fmt.get("filesize") or 0) > MAX_FILE_SIZE_BYTES
    ]
    if too_big:
        logging.info("Plain files over Telegram's limit left out: %s", too_big)
    return info_dict


def vk_cookie_file() -> Path | None:
    """
    Cookies of a logged-in VK account, when a person has put a file in place.

    :return: One of cookies/vk/cookies*.txt, None when there is none.
    """
    files = [
        path for path in VK_COOKIES_DIR.glob("cookies*.txt") if is_cookie_file(path)
    ]
    return secrets.choice(files) if files else None


class VKAccessMixin:
    """
    What VK sources share: closed videos, the account and clean titles.

    The account is used only for a video VK refuses to a guest, never for
    every request: an account that reads hundreds of videos a day from a
    server address gets locked.
    """

    _yt_dlp: Any
    _telegram_id: int
    _telegram_bot_controller: Any
    _proxy: str | None
    _vk_account_used: bool = False

    def _use_vk_account(self) -> bool:
        """
        Switch this request to the account's cookies.

        :return: False when there are no cookies or they are already in use.
        """
        if self._vk_account_used:
            return False
        cookie_file = vk_cookie_file()
        if not cookie_file:
            return False
        self._vk_account_used = True
        logging.info("VK asks for a login, using the account cookies")
        self._yt_dlp = self._create_yt_dlp(  # type: ignore[attr-defined]
            {**self._yt_dlp.params, "cookiefile": str(cookie_file)},
        )
        return True

    def _prepare_info(self, info_dict: dict[str, Any]) -> dict[str, Any]:
        """
        Remove markup from the title: VK Music titles carry «<span …>».

        :param info_dict: The dictionary from yt_dlp.extract_info.
        :return: The same dictionary with a plain title.
        """
        for key in ("title", "fulltitle"):
            if isinstance(info_dict.get(key), str):
                info_dict[key] = TAG_RE.sub("", info_dict[key]).strip()
        return info_dict

    async def _complete_info(self, info_dict: dict[str, Any]) -> dict[str, Any]:
        return await fill_plain_file_sizes(info_dict, self._proxy)

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Get video info; a closed video gets its own answer, a login is tried.

        :param url: URL of the video.
        :return: Dictionary with video information or None on failure.
        """
        try:
            return await super().get_video_info(url)  # type: ignore[misc]
        except DownloadError as e:
            text = str(e).lower()
            if SIGN_IN_TEXT in text and self._use_vk_account():
                return await self.get_video_info(url)
            if any(hint in text for hint in VK_GONE_HINTS):
                logging.info("VK video is closed or gone: %s (%s)", url, e)
                await self.delete_processing_message()  # type: ignore[attr-defined]
                await self._telegram_bot_controller.send_content_not_found_error(
                    telegram_id=self._telegram_id,
                )
                return None
            raise
