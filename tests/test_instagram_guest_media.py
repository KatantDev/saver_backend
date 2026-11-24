from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders import instagram_ydl_source
from saver_backend.services.downloaders.instagram_session import YDL_COOKIES_FILE
from saver_backend.services.downloaders.instagram_ydl_source import (
    InstagramYdlController,
    full_size_picture,
    is_content_unavailable,
)
from saver_backend.services.downloaders.schema import PhotoDTO, VideoDTO

URL = "https://www.instagram.com/p/DfIY71Lku89/"
CDN = "https://scontent.cdninstagram.com/v/t51/"


def _thumbnails(name: str) -> list[dict[str, Any]]:
    return [
        {"url": f"{CDN}{name}.jpg?stp=dst-jpg_e35_s150x150_tt6"},
        {"url": f"{CDN}{name}.jpg?stp=dst-jpg_e35_p640x640_tt6"},
        {"url": f"{CDN}{name}.jpg?stp=dst-jpg_e35_tt6"},
    ]


def _controller(use_session: bool = True) -> InstagramYdlController:
    return InstagramYdlController(
        resolution=Resolution(
            source=SourceEnum.INSTAGRAM_YDL,
            url=URL,
            metadata={"code": "DfIY71Lku89", "type": "p"},
        ),
        telegram_bot_controller=AsyncMock(),
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=AsyncMock(),
        use_session=use_session,
    )


def test_original_picture_is_picked_among_thumbnails() -> None:
    """The scaled copies carry their size in the address, the original does not."""
    assert full_size_picture(_thumbnails("a")) == f"{CDN}a.jpg?stp=dst-jpg_e35_tt6"
    only_scaled = _thumbnails("a")[:2]
    assert full_size_picture(only_scaled) == only_scaled[-1]["url"]
    assert full_size_picture([]) is None


def test_private_account_counts_as_unavailable() -> None:
    """A post of a private account is answered «private or deleted»."""
    error = DownloadError(
        "ERROR: [Instagram] x: This content is only available for registered "
        "users who follow this account.",
    )
    assert is_content_unavailable(error) is True


@pytest.mark.anyio
async def test_single_photo_post_becomes_a_photo() -> None:
    """«There is no video in this post» was 67 failures in three days."""
    controller = _controller()
    info = {"id": "DfHyVL-F1uT", "formats": [], "thumbnails": _thumbnails("a")}

    with patch.object(controller, "_extract_unprocessed", return_value=info):
        media = await controller.fetch_media(URL)

    assert len(media) == 1
    assert isinstance(media[0], PhotoDTO)
    assert media[0].media_url == f"{CDN}a.jpg?stp=dst-jpg_e35_tt6"
    assert media[0].url == URL
    await controller.close()


@pytest.mark.anyio
async def test_carousel_keeps_the_order_of_photos_and_videos() -> None:
    """«No video formats found» was 75 failures in three days."""
    controller = _controller()
    info = {
        "id": "DfIY71Lku89",
        "_type": "playlist",
        "webpage_url": URL,
        "entries": [
            {"id": "a", "formats": [], "thumbnails": _thumbnails("a")},
            {"id": "b", "formats": [{"format_id": "0", "url": "https://cdn/b.mp4"}]},
            {"id": "c", "formats": [], "thumbnails": _thumbnails("c")},
        ],
    }
    video = VideoDTO(source_id="b", direct_download_url="https://cdn/b.mp4")

    with (
        patch.object(controller, "_extract_unprocessed", return_value=info),
        patch.object(controller, "_video_from_item", return_value=video) as build,
    ):
        media = await controller.fetch_media(URL)

    assert [type(item) for item in media] == [PhotoDTO, VideoDTO, PhotoDTO]
    assert build.call_args.args[0]["webpage_url"] == URL
    await controller.close()


@pytest.mark.anyio
async def test_rate_limit_is_retried_through_another_proxy() -> None:
    """A 429 on one address does not end the request."""
    controller = _controller()
    info = {"id": "a", "formats": [], "thumbnails": _thumbnails("a")}
    limit = DownloadError("ERROR: [Instagram] x: HTTP Error 429: Too Many Requests")

    with (
        patch.object(
            controller,
            "_extract_unprocessed",
            side_effect=[limit, info],
        ) as extract,
        patch.object(controller, "_switch_proxy_for_retry") as switch,
        patch.object(instagram_ydl_source.asyncio, "sleep", AsyncMock()),
    ):
        media = await controller.fetch_media(URL)

    assert extract.call_count == 2
    switch.assert_called_once()
    assert len(media) == 1
    await controller.close()


@pytest.mark.anyio
async def test_unavailable_post_is_not_retried() -> None:
    """A post Instagram reports as closed is not asked for again."""
    controller = _controller()
    gone = DownloadError("ERROR: [Instagram] x: Instagram sent an empty media response")

    with (
        patch.object(controller, "_extract_unprocessed", side_effect=gone) as extract,
        pytest.raises(DownloadError),
    ):
        await controller.fetch_media(URL)

    assert extract.call_count == 1
    await controller.close()


@pytest.mark.anyio
async def test_guest_request_leaves_the_account_session_alone(tmp_path: Any) -> None:
    """The first attempt must not spend the service account."""
    session_file = tmp_path / "cookies_session.txt"
    session_file.write_text("# Netscape HTTP Cookie File\n")

    with patch.object(instagram_ydl_source, "YDL_COOKIES_FILE", session_file):
        guest = _controller(use_session=False)
        account = _controller(use_session=True)

    assert guest._base_options.get("cookiefile") != str(session_file)  # noqa: SLF001
    assert account._base_options.get("cookiefile") == str(session_file)  # noqa: SLF001
    assert YDL_COOKIES_FILE.name == "cookies_session.txt"
