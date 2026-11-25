"""
Find the video behind a link that only points to it.

Yandex Video shows videos hosted elsewhere (YouTube, VK, Rutube, OK) under its
own address, and share.google is a redirect made by the share button of the
Google app. People send such links as they are; the bot answered «not
supported» (55 times on prod on 21-23.11) although it downloads the video
itself.
"""

import json
import logging
import re
from urllib.parse import urlparse

import httpx
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

YANDEX_VIDEO_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:yandex\.(?:ru|kz|uz|by|com|com\.tr)|ya\.ru)"
    r"/video/(?:touch/)?preview/\d+",
    re.IGNORECASE,
)
SHARE_GOOGLE_RE = re.compile(r"^https?://share\.google/[A-Za-z0-9_-]+", re.IGNORECASE)
VIDEO_URL_RE = re.compile(r'"videoUrl":"((?:[^"\\]|\\.)+)"')
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

UNWRAP_TIMEOUT = 8
# share.google answers with a redirect to google.com, which redirects on
MAX_GOOGLE_HOPS = 3
GOOGLE_HOSTS = ("share.google", "google.com", "www.google.com")


def wrapped_link(text: str) -> str | None:
    """
    The first link of a message when it only points to a video elsewhere.

    :param text: Text of the message.
    :return: The link to unwrap or None.
    """
    found = URL_RE.search(text or "")
    if not found:
        return None
    url = found.group(0)
    if YANDEX_VIDEO_RE.match(url) or SHARE_GOOGLE_RE.match(url):
        return url
    return None


async def _yandex_video_target(url: str) -> str | None:
    # Yandex shows a captcha to a plain HTTP client and the page to a browser
    try:
        async with AsyncSession(
            impersonate="chrome", timeout=UNWRAP_TIMEOUT
        ) as session:
            response = await session.get(url)
    except RequestException as e:
        logging.warning("Yandex Video page was not read: %s", type(e).__name__)
        return None
    found = VIDEO_URL_RE.search(response.text)
    if not found:
        logging.warning(
            "Yandex Video page has no video link (status %s, captcha: %s)",
            response.status_code,
            "showcaptcha" in str(response.url),
        )
        return None
    try:
        target = json.loads(f'"{found.group(1)}"')
    except ValueError:
        return None
    return target if isinstance(target, str) else None


async def _share_google_target(url: str) -> str | None:
    # Redirects are followed only inside Google: the first address outside it
    # is the answer and is not requested from here
    current = url
    try:
        async with httpx.AsyncClient(
            timeout=UNWRAP_TIMEOUT,
            follow_redirects=False,
        ) as client:
            for _hop in range(MAX_GOOGLE_HOPS):
                response = await client.get(current)
                location = response.headers.get("location")
                if not response.is_redirect or not location:
                    return None
                host = (urlparse(location).hostname or "").lower()
                if host not in GOOGLE_HOSTS:
                    return location
                current = location
    except httpx.HTTPError as e:
        logging.warning("share.google link was not followed: %s", type(e).__name__)
    return None


async def unwrap(url: str) -> str | None:
    """
    Address of the video a wrapping link points to.

    :param url: A Yandex Video preview link or a share.google link.
    :return: The address of the video, None when it could not be found.
    """
    if YANDEX_VIDEO_RE.match(url):
        target = await _yandex_video_target(url)
    elif SHARE_GOOGLE_RE.match(url):
        target = await _share_google_target(url)
    else:
        return None
    if not target or not target.lower().startswith(("http://", "https://")):
        return None
    return target
