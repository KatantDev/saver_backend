# ruff: noqa: SLF001
import base64
from typing import Any
from unittest.mock import AsyncMock

import pytest

from saver_backend.entities.enums import InstagramContentTypeEnum, SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.instagram_indown_source import (
    HIGHLIGHT_ITEMS_WITHOUT_STORY,
    InstagramInDownController,
    media_id_in,
)
from saver_backend.services.downloaders.resolver import SourceResolver

HIGHLIGHT_ID = "18198768481378247"
SHARED = base64.urlsafe_b64encode(f"highlight:{HIGHLIGHT_ID}".encode()).decode()


def _cache_key(media_id: int) -> str:
    return base64.urlsafe_b64encode(str(media_id).encode()).decode().rstrip("=")


def _item(media_id: int, video: bool) -> str:
    key = f"ig_cache_key%3D{_cache_key(media_id)}.3-ccb7-5"
    picture = (
        f"https://d3.indown.io/fetch?url=https%3A%2F%2Fcdn%2Fp{media_id}.jpg%3F{key}"
    )
    preview = (
        f'<video poster="{picture}&amp;is_download=0"></video>'
        if video
        else f'<img src="{picture}&amp;is_download=0">'
    )
    extension = "mp4" if video else "jpg"
    return (
        f'<div class="item">{preview}<div class="btn-group-vertical">'
        f'<a href="https://cdn/file{media_id}.{extension}?x=1">Download</a></div></div>'
    )


def _controller(metadata: dict[str, Any], url: str) -> InstagramInDownController:
    return InstagramInDownController(
        resolution=Resolution(
            source=SourceEnum.INSTAGRAM_INDOWN,
            url=url,
            metadata=metadata,
        ),
        telegram_bot_controller=AsyncMock(),
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=AsyncMock(),
        message_id=1,
    )


def test_shared_highlight_link_is_recognised() -> None:
    """The share button of a highlight gives /s/<base64> with the story id."""
    url = (
        f"https://www.instagram.com/s/{SHARED}"
        "?story_media_id=4008398038045079440_55555555&bqrl=NGN1bWV6"
    )
    resolution = SourceResolver().resolve(url)

    assert resolution.source == SourceEnum.INSTAGRAM_INDOWN
    assert resolution.metadata["type"] == InstagramContentTypeEnum.HIGHLIGHTS
    assert resolution.metadata["code"] == HIGHLIGHT_ID
    assert resolution.metadata["story"] == "4008398038045079440"
    assert resolution.url == (
        f"https://www.instagram.com/s/{SHARED}?story_media_id=4008398038045079440"
    )


def test_highlight_link_without_a_story_is_recognised() -> None:
    """A link to the whole highlight has no story id."""
    resolution = SourceResolver().resolve(f"https://www.instagram.com/s/{SHARED}?a=b")

    assert resolution.metadata["type"] == InstagramContentTypeEnum.HIGHLIGHTS
    assert resolution.metadata["story"] == ""
    assert resolution.url == f"https://www.instagram.com/s/{SHARED}"


def test_plain_highlight_link_is_recognised() -> None:
    """The address a browser shows for a highlight."""
    resolution = SourceResolver().resolve(
        f"https://www.instagram.com/stories/highlights/{HIGHLIGHT_ID}/",
    )

    assert resolution.metadata["type"] == InstagramContentTypeEnum.HIGHLIGHTS
    assert resolution.metadata["code"] == HIGHLIGHT_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/s/aGVsbG8=",
        "https://www.instagram.com/s/%%%",
        "https://www.instagram.com/nasa/",
    ],
)
def test_other_links_stay_unsupported(url: str) -> None:
    """Only a highlight is read from a /s/ link; a profile is not a download."""
    assert SourceResolver().resolve(url).source == SourceEnum.UNSUPPORTED


def test_story_link_is_still_a_story() -> None:
    """«highlights» is not mistaken for a user name and stories keep working."""
    resolution = SourceResolver().resolve(
        "https://www.instagram.com/stories/nasa/3500000000000000000/",
    )
    assert resolution.metadata["type"] == InstagramContentTypeEnum.STORIES


def test_media_id_is_read_from_a_poster() -> None:
    """The poster of a video carries the media id behind indown.io's address."""
    assert media_id_in(_item(4008398038045079440, video=True)) == 4008398038045079440
    assert media_id_in("<div>nothing here</div>") is None


@pytest.mark.anyio
async def test_the_watched_story_is_picked_from_the_highlight() -> None:
    """Of 40 items only the story the link points to is sent, video or photo."""
    controller = _controller(
        {
            "type": InstagramContentTypeEnum.HIGHLIGHTS,
            "code": HIGHLIGHT_ID,
            "story": "103",
        },
        f"https://www.instagram.com/s/{SHARED}?story_media_id=103",
    )
    html = "".join(
        [_item(101, video=False), _item(102, video=True), _item(103, video=True)],
    )

    media = controller._extract_media_dtos(html, controller._resolution.url, "x")

    assert len(media) == 1
    assert media[0].direct_download_url == "https://cdn/file103.mp4?x=1"  # type: ignore[union-attr]
    assert controller._extract_source_id(controller._resolution.url) == (
        f"hl{HIGHLIGHT_ID}_103"
    )
    await controller.close()


@pytest.mark.anyio
async def test_highlight_without_a_story_sends_the_first_items() -> None:
    """A link to the whole highlight is not answered with dozens of albums."""
    controller = _controller(
        {
            "type": InstagramContentTypeEnum.HIGHLIGHTS,
            "code": HIGHLIGHT_ID,
            "story": "",
        },
        f"https://www.instagram.com/s/{SHARED}",
    )
    html = "".join(_item(100 + n, video=False) for n in range(25))

    media = controller._extract_media_dtos(html, controller._resolution.url, "x")

    assert len(media) == HIGHLIGHT_ITEMS_WITHOUT_STORY
    assert controller._extract_source_id("x") == f"hl{HIGHLIGHT_ID}_all"
    await controller.close()


@pytest.mark.anyio
async def test_story_video_is_matched_by_its_poster() -> None:
    """A story video used to be guessed by its place among the photos."""
    controller = _controller(
        {"type": InstagramContentTypeEnum.STORIES, "user": "nasa", "code": "202"},
        "https://www.instagram.com/stories/nasa/202/",
    )
    html = "".join(
        [_item(201, video=True), _item(202, video=True), _item(203, video=True)],
    )

    media = controller._extract_media_dtos(html, controller._resolution.url, "202")

    assert len(media) == 1
    assert media[0].direct_download_url == "https://cdn/file202.mp4?x=1"  # type: ignore[union-attr]
    await controller.close()


@pytest.mark.anyio
async def test_post_carousel_is_sent_whole() -> None:
    """Posts are not filtered: every item of a carousel goes out."""
    controller = _controller(
        {"type": InstagramContentTypeEnum.POST, "code": "DfIY71Lku89"},
        "https://www.instagram.com/p/DfIY71Lku89/",
    )
    html = "".join(_item(300 + n, video=False) for n in range(4))

    media = controller._extract_media_dtos(html, controller._resolution.url, "x")

    assert len(media) == 4
    await controller.close()
