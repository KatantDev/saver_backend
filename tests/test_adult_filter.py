from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.adult_filter import (
    is_gay_info,
    is_gay_page,
    needs_page_check,
)
from saver_backend.services.downloaders.adult_ydl_source import AdultYdlController
from saver_backend.services.downloaders.resolver import SourceResolver
from saver_backend.services.downloaders.ydl_source import YtDlpController

# yt-dlp info of real pornhub videos, read from the prod server on 23.11
PORNHUB_GAY = {
    "title": "x",
    "categories": ["Amateur", "Feet", "Gay", "Latino"],
    "tags": ["tickling", "kink", "gay amateur", "straight", "bdsm"],
}
PORNHUB_STRAIGHT = {
    "title": "x",
    "categories": ["Amateur", "Big Dick", "Blowjob", "HD Porn", "Hardcore"],
    "tags": ["brunette", "teen", "missionary", "pov"],
}
XVIDEOS_GAY_PAGE = '{"page_main_cat":"gay","pagefilter":"gay","orientation":"straight"}'
XVIDEOS_STRAIGHT_PAGE = (
    '{"page_main_cat":"straight","pagefilter":"straight",'
    '"user_main_cat":"straight"} <a href="/switch-sexual-orientation/gay/straight">'
)
XNXX_GAY_PAGE = '{"categories":"gay","orientation":"straight"}'


@pytest.mark.parametrize(
    "url",
    [
        "https://www.pornhub.com/view_video.php?viewkey=abc",
        "https://xvideos.com/video123/title",
        "https://www.redgifs.com/watch/x",
    ],
)
def test_adult_sites_are_supported_again(url: str) -> None:
    """Bulat 23.11: adult videos may be downloaded again."""
    assert SourceResolver().resolve(url).source == SourceEnum.ADULT_YDL


def test_bare_adult_stream_stays_unsupported() -> None:
    """A .m3u8 stream has no page to check the category on."""
    resolution = SourceResolver().resolve("https://cdn.xvideos.com/hls/master.m3u8")

    assert resolution.source == SourceEnum.UNSUPPORTED


@pytest.mark.parametrize(
    ("url", "source"),
    [
        ("https://youtu.be/ErAqN6gXqZQ", SourceEnum.YOUTUBE_VIDEO_YDL),
        ("https://example.com/live/stream.m3u8", SourceEnum.M3U8_YDL),
    ],
)
def test_other_sites_are_untouched(url: str, source: SourceEnum) -> None:
    """Other sources keep resolving as before."""
    assert SourceResolver().resolve(url).source == source


def test_pornhub_gay_category_is_recognised() -> None:
    """The «straight» tag on a gay video does not let it through."""
    assert is_gay_info(PORNHUB_GAY)


def test_pornhub_other_categories_pass() -> None:
    """Videos outside the gay category are downloaded."""
    assert not is_gay_info(PORNHUB_STRAIGHT)


@pytest.mark.parametrize("title", ["Hot gay couple", "Гей вечеринка", "gay-porn"])
def test_gay_word_in_title_is_recognised(title: str) -> None:
    """Sites without categories still name the category in the title."""
    assert is_gay_info({"title": title})


@pytest.mark.parametrize("title", ["Marvin Gaye cover", "Holiday trip", None])
def test_other_titles_pass(title: str | None) -> None:
    """Only the word itself counts."""
    assert not is_gay_info({"title": title, "categories": None, "tags": None})


def test_xvideos_and_xnxx_pages_are_read() -> None:
    """Their category is only on the page; the orientation switch is ignored."""
    assert is_gay_page(XVIDEOS_GAY_PAGE)
    assert is_gay_page(XNXX_GAY_PAGE)
    assert not is_gay_page(XVIDEOS_STRAIGHT_PAGE)


def test_page_is_checked_only_where_needed() -> None:
    """Pornhub's info is enough, xvideos and xnxx need the page."""
    assert needs_page_check("https://www.xvideos.com/video.abc/title")
    assert needs_page_check("https://de.xnxx.com/video-1/title")
    assert not needs_page_check("https://www.pornhub.com/view_video.php?viewkey=a")


def _controller() -> Any:
    source: Any = AdultYdlController.__new__(AdultYdlController)
    source._telegram_id = 1  # noqa: SLF001
    source._telegram_bot_controller = MagicMock()  # noqa: SLF001
    source._telegram_bot_controller.send_content_blocked = AsyncMock()  # noqa: SLF001
    source.delete_processing_message = AsyncMock()
    source._video = MagicMock()  # noqa: SLF001
    return source


@pytest.mark.anyio
async def test_gay_video_is_refused_before_download() -> None:
    """The user gets the refusal and nothing is downloaded or sent from cache."""
    source = _controller()
    source._page_is_gay = AsyncMock(return_value=False)  # noqa: SLF001
    with patch.object(
        YtDlpController,
        "get_video_info",
        AsyncMock(return_value=PORNHUB_GAY),
    ):
        info = await AdultYdlController.get_video_info(source, "https://pornhub.com/v")

    assert info is None
    assert source._video is None  # noqa: SLF001
    source._telegram_bot_controller.send_content_blocked.assert_awaited_once()  # noqa: SLF001


@pytest.mark.anyio
async def test_gay_page_is_refused() -> None:
    """xvideos: the info says nothing, the page says gay."""
    source = _controller()
    source._page_is_gay = AsyncMock(return_value=True)  # noqa: SLF001
    with patch.object(
        YtDlpController,
        "get_video_info",
        AsyncMock(return_value={"title": "x", "webpage_url": "https://xvideos.com/v"}),
    ):
        info = await AdultYdlController.get_video_info(source, "https://xvideos.com/v")

    assert info is None
    source._page_is_gay.assert_awaited_once_with("https://xvideos.com/v")  # noqa: SLF001


@pytest.mark.anyio
async def test_other_video_goes_on() -> None:
    """A video outside the gay category is downloaded as usual."""
    source = _controller()
    source._page_is_gay = AsyncMock(return_value=False)  # noqa: SLF001
    with patch.object(
        YtDlpController,
        "get_video_info",
        AsyncMock(return_value=PORNHUB_STRAIGHT),
    ):
        info = await AdultYdlController.get_video_info(source, "https://pornhub.com/v")

    assert info == PORNHUB_STRAIGHT
    source._telegram_bot_controller.send_content_blocked.assert_not_awaited()  # noqa: SLF001
