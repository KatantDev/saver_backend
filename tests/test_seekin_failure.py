from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from saver_backend.services.downloaders.seekinai_source import (
    SeekinAiController,
    pick_kwai_page_urls,
)

FILE = (
    "/upic/2026/01/19/21/BMjAyNjAxMTkyMTQ1MzFfMTUwMDAxNzI3MTg1NzA3XzE1MDExMDI2MDUw"
    "NDk0MF8yXzM=_b_B9118c2b863ab35e32b5207825db53e09.mp4"
)
SEEKIN_URL = f"https://txmov2.a.kwimgs.com{FILE}?tag=1-1795414623-sp-0-x"
PAGE_URL = f"https://ak-br-cdn.kwai.net{FILE}?tag=1-1795414721-s-1-y"
OTHER_VIDEO = (
    "https://ak-br-cdn.kwai.net/upic/2026/11/18/22/BMjAyNjExMTgyMjAyMjNf"
    "_b_B66272a6e00588d3c4ccee2f096dbb7a9.mp4?tag=1-1795414683-s-1-z"
)


@pytest.mark.anyio
async def test_failed_download_tells_the_user() -> None:
    """Prod 21.11: a Kwai video failed to download and the user saw 72% forever."""
    source = MagicMock()
    source._resolution.metadata = {"code": "5213629630362183094"}  # noqa: SLF001
    source.send_video_from_cache = AsyncMock(return_value=False)
    source._download_video_by_url = AsyncMock(return_value=None)  # noqa: SLF001
    source._fallback_media_urls = AsyncMock(return_value=[])  # noqa: SLF001
    source._send_error_message = AsyncMock()  # noqa: SLF001
    source._send_video = AsyncMock()  # noqa: SLF001
    data = MagicMock()
    data.medias = [MagicMock(url="https://cdn.example/v.mp4")]

    await SeekinAiController._handle_video(source, data)  # noqa: SLF001

    source._send_error_message.assert_awaited_once()  # noqa: SLF001
    source._send_video.assert_not_awaited()  # noqa: SLF001


def test_kwai_page_gives_the_same_file_on_another_host() -> None:
    """Prod 23.11: seekin.ai's link answered 404, the page's link downloaded."""
    escaped = PAGE_URL.replace("/", "\\u002F")
    page = (
        f'<video src="{PAGE_URL}" preload="auto">'
        f'{{cdn:A,url:"{escaped}"}},{{cdn:B,url:"{OTHER_VIDEO}"}}'
        f'{{cdn:C,url:"{SEEKIN_URL}"}}'
    )

    assert pick_kwai_page_urls(page, SEEKIN_URL) == [PAGE_URL]


def test_kwai_page_without_the_file_gives_nothing() -> None:
    """Recommended videos on the page are never sent instead of the asked one."""
    assert pick_kwai_page_urls(f'<a href="{OTHER_VIDEO}">', SEEKIN_URL) == []


@pytest.mark.anyio
async def test_page_link_is_used_when_seekin_link_fails() -> None:
    """The video is sent from the second link and no error is shown."""
    source = MagicMock()
    source._resolution.metadata = {"code": "5213629630362183094"}  # noqa: SLF001
    source._resolution.url = "https://www.kwai.com/@a/video/5213629630362183094"  # noqa: SLF001
    source.send_video_from_cache = AsyncMock(return_value=False)
    video = MagicMock()
    source._download_video_by_url = AsyncMock(side_effect=[None, video])  # noqa: SLF001
    source._fallback_media_urls = AsyncMock(return_value=[PAGE_URL])  # noqa: SLF001
    source._send_error_message = AsyncMock()  # noqa: SLF001
    source._send_video = AsyncMock()  # noqa: SLF001
    data = MagicMock()
    data.medias = [MagicMock(url=SEEKIN_URL)]

    with patch(
        "saver_backend.services.downloaders.seekinai_source.VideoDTO.from_seekinai",
        return_value=video,
    ):
        await SeekinAiController._handle_video(source, data)  # noqa: SLF001

    source._download_video_by_url.assert_awaited_with(PAGE_URL)  # noqa: SLF001
    source._send_video.assert_awaited_once_with(video)  # noqa: SLF001
    source._send_error_message.assert_not_awaited()  # noqa: SLF001
