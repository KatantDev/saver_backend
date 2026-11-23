"""
Recognise gay-category videos on adult websites.

Bulat (23.11.2026): adult videos may be downloaded, except the gay category.

What the sites tell us (checked from the prod server on 23.11.2026):

- pornhub: yt-dlp returns ``categories`` with "Gay" and tags like "gay porn";
- xvideos: yt-dlp returns neither categories nor tags, but the video page has
  ``"page_main_cat":"gay"`` (``"straight"`` on other videos);
- xnxx: the video page has ``"categories":"gay"``.
"""

import re
from typing import Any

from saver_backend.services.failure_log import host_of

# gay, gay-porn, gayporn and the Russian word; not Gaye.
GAY_WORD_RE = re.compile(
    r"(?<![a-zа-яё])(?:gay(?!e)|гей)",  # noqa: RUF001
    re.IGNORECASE,
)
GAY_PAGE_RE = re.compile(
    r'"(?:page_main_cat|categories)"\s*:\s*"gay"',
    re.IGNORECASE,
)
PAGE_CHECK_HOSTS = ("xvideos.com", "xnxx.com")


def is_gay_info(info: dict[str, Any]) -> bool:
    """
    Whether yt-dlp's info marks the video as gay category.

    Only the word gay counts: a gay video on pornhub had the tag "straight", so
    other tags say nothing.

    :param info: Info returned by yt-dlp.
    :return: True for a gay-category video.
    """
    texts: list[str] = []
    for key in ("categories", "tags"):
        value = info.get(key)
        if isinstance(value, list):
            texts.extend(str(item) for item in value)
    texts.append(str(info.get("title") or ""))
    return any(GAY_WORD_RE.search(text) for text in texts)


def needs_page_check(url: str) -> bool:
    """Sites whose category is only on the video page, not in yt-dlp's info."""
    host = host_of(url)
    return any(host == h or host.endswith("." + h) for h in PAGE_CHECK_HOSTS)


def is_gay_page(page: str) -> bool:
    """
    Whether the video page marks the video as gay category.

    :param page: HTML of the video page.
    :return: True for a gay-category video.
    """
    return GAY_PAGE_RE.search(page) is not None
