# ruff: noqa: SLF001
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.consts import MAX_FILE_SIZE_BYTES
from saver_backend.services.downloaders import tiktok_api_source, vk_common
from saver_backend.services.downloaders.schema import TikWMData, VideoDTO
from saver_backend.services.downloaders.tiktok_api_source import (
    TIKWM_INLINE_TIMEOUT,
    TIKWM_TIMEOUT,
    TikTokAPIController,
)
from saver_backend.services.downloaders.vk_common import (
    VKAccessMixin,
    fill_plain_file_sizes,
)
from saver_backend.services.downloaders.vk_video_ydl_source import VKVideoYdlController
from saver_backend.services.downloaders.ydl_source import YtDlpController

GB = 1024**3
PHOTO_URL = "https://www.tiktok.com/@nasa/photo/7709651606630944013"


def _stream(height: int, size: int) -> dict[str, Any]:
    return {
        "format_id": f"hls-{height}",
        "ext": "mp4",
        "vcodec": "avc1",
        "acodec": "mp4a",
        "width": height * 16 // 9,
        "height": height,
        "protocol": "m3u8_native",
        "filesize": size,
    }


def _plain(height: int, size: int | None = None) -> dict[str, Any]:
    return {
        "format_id": f"url{height}",
        "ext": "mp4",
        "vcodec": "avc1",
        "acodec": "mp4a",
        "height": height,
        "protocol": "https",
        "url": f"https://vk/{height}.mp4",
        "filesize": size,
    }


def _labels(formats: list[dict[str, Any]], duration: int = 7200) -> list[str]:
    video = VideoDTO.from_yt_dlp(
        info={"id": "1", "title": "match", "duration": duration, "formats": formats},
    )
    return sorted(video.unique_formats)


def test_plain_file_of_an_oversized_quality_is_not_offered() -> None:
    """The 1440p stream is 2.65 GB: its plain twin must not stay as the button."""
    formats = [
        _stream(1080, GB),
        _plain(1080),
        _stream(1440, int(2.65 * GB)),
        _plain(1440),
    ]
    assert _labels(formats) == ["1080p"]


def test_measured_plain_file_over_the_limit_is_not_offered() -> None:
    """A video with plain files only: the size asked from the server decides."""
    formats = [_plain(720, GB), _plain(1080, MAX_FILE_SIZE_BYTES + 1)]
    assert _labels(formats) == ["720p"]


@pytest.mark.anyio
async def test_sizes_of_big_plain_files_are_asked() -> None:
    """Long video, high quality, no size: one HEAD request per file."""
    info = {"duration": 7200, "formats": [_plain(360), _plain(720), _plain(1080)]}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        size = 3 * GB if "1080" in str(request.url) else GB
        return httpx.Response(200, headers={"content-length": str(size)})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("proxy", None)
        return real_client(transport=transport, **kwargs)

    with patch.object(vk_common.httpx, "AsyncClient", client):
        await fill_plain_file_sizes(info, proxy=None)

    assert sorted(asked) == ["https://vk/1080.mp4", "https://vk/720.mp4"]
    assert info["formats"][0]["filesize"] is None
    assert info["formats"][1]["filesize"] == GB
    assert info["formats"][2]["filesize"] == 3 * GB


@pytest.mark.anyio
async def test_short_videos_are_not_measured() -> None:
    """A ten-minute video cannot reach 2 GB: no extra requests."""
    info = {"duration": 600, "formats": [_plain(1080)]}
    with patch.object(vk_common.httpx, "AsyncClient", MagicMock()) as client:
        await fill_plain_file_sizes(info, proxy=None)
    client.assert_not_called()


def _vk() -> Any:
    controller: Any = VKVideoYdlController.__new__(VKVideoYdlController)
    controller._telegram_id = 1
    controller._proxy = None
    controller._telegram_bot_controller = MagicMock()
    controller._telegram_bot_controller.send_content_not_found_error = AsyncMock()
    controller.delete_processing_message = AsyncMock()
    controller._yt_dlp = MagicMock(params={})
    controller._create_yt_dlp = MagicMock(return_value=MagicMock(params={}))
    return controller


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text",
    [
        "ERROR: [vk] This video is unavailable because its creator has been blocked",
        "ERROR: [vk] This video has been deleted and is no longer available.",
        "ERROR: [vk] Video only available to followers",
        "ERROR: [vk] Access restricted",
    ],
)
async def test_closed_vk_video_is_private_or_deleted(text: str) -> None:
    """The user is told the video is closed, not «we informed the developers»."""
    controller = _vk()
    base = AsyncMock(side_effect=DownloadError(text))

    with patch.object(YtDlpController, "get_video_info", base):
        result = await controller.get_video_info("https://vk.com/video1_1")

    assert result is None
    send = controller._telegram_bot_controller.send_content_not_found_error
    send.assert_awaited_once()


@pytest.mark.anyio
async def test_login_only_video_is_retried_with_the_account(tmp_path: Path) -> None:
    """With a cookie file in place a signed-in-only video is read through it."""
    controller = _vk()
    cookie_file = tmp_path / "cookies1.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n.vk.com\tTRUE\t/\tTRUE\t0\ta\tb\n"
    )
    info = {"id": "1"}
    base = AsyncMock(
        side_effect=[
            DownloadError("ERROR: [vk] Video only available to signed-in users"),
            info,
        ],
    )

    with (
        patch.object(vk_common, "VK_COOKIES_DIR", tmp_path),
        patch.object(YtDlpController, "get_video_info", base),
    ):
        result = await controller.get_video_info("https://vk.com/video1_1")

    assert result == info
    params = controller._create_yt_dlp.call_args.args[0]
    assert params["cookiefile"] == str(cookie_file)


@pytest.mark.anyio
async def test_login_only_video_without_cookies_is_an_error(tmp_path: Path) -> None:
    """Without a cookie file the error goes up as before."""
    controller = _vk()
    base = AsyncMock(
        side_effect=DownloadError(
            "ERROR: [vk] Video only available to signed-in users"
        ),
    )

    with (
        patch.object(vk_common, "VK_COOKIES_DIR", tmp_path),
        patch.object(YtDlpController, "get_video_info", base),
        pytest.raises(DownloadError),
    ):
        await controller.get_video_info("https://vk.com/video1_1")

    assert base.await_count == 1


def test_markup_is_removed_from_vk_titles() -> None:
    """VK Music titles came as «<span …>Artist</span> — Track»."""
    info = {"title": '<span class="a">Artist</span> — Track', "fulltitle": None}
    assert VKAccessMixin._prepare_info(MagicMock(), info)["title"] == "Artist — Track"


def _tiktok(url: str, inline: bool = False) -> TikTokAPIController:
    controller = TikTokAPIController(
        resolution=Resolution(
            source=SourceEnum.TIKTOK,
            url=url,
            metadata={"code": "x"},
        ),
        telegram_bot_controller=AsyncMock(),
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=AsyncMock(),
        message_id=5,
        inline_query_id="q" if inline else None,
    )
    controller._create_history_entry = AsyncMock()  # type: ignore[method-assign]
    return controller


@pytest.mark.anyio
async def test_photo_post_is_sent_without_tikwm(tmp_path: Path) -> None:
    """tikwm.com is down: the pictures come from TikTok's own page."""
    controller = _tiktok(PHOTO_URL)
    controller._download_directory = tmp_path
    data = TikWMData(
        id="7709651606630944013",
        title="moon",
        images=["https://cdn/1.jpeg", "https://cdn/2.jpeg"],
        music="https://cdn/m.mp3",
    )
    picture = MagicMock(content=b"jpeg", raise_for_status=lambda: None)
    ydl_cls = MagicMock()

    with (
        patch.object(controller, "_read_photo_post", return_value=data),
        patch.object(controller._client, "get", AsyncMock(return_value=picture)),
        patch.object(tiktok_api_source, "TikTokYdlController", ydl_cls),
    ):
        await controller._fallback_to_ydl()

    telegram = controller._telegram_bot_controller
    files = telegram.send_finish_downloading_group.await_args.kwargs["files"]
    assert len(files) == 2
    assert all(photo.media_url is None for photo in files)
    assert list(tmp_path.iterdir()) == []
    telegram.send_finish_downloading_audio.assert_awaited_once()
    ydl_cls.assert_not_called()
    await controller.close()


@pytest.mark.anyio
async def test_short_link_to_a_photo_post_is_followed() -> None:
    """A vt.tiktok.com link is opened to see that it leads to a photo post."""
    controller = _tiktok("https://vt.tiktok.com/ZSabcdef/")
    answer = MagicMock(url=PHOTO_URL + "?_r=1")

    with patch.object(controller._client, "get", AsyncMock(return_value=answer)):
        assert await controller._photo_post_url() == PHOTO_URL + "?_r=1"

    await controller.close()


@pytest.mark.anyio
async def test_video_goes_to_yt_dlp_as_before() -> None:
    """A video link is not treated as a photo post."""
    controller = _tiktok("https://www.tiktok.com/@nasa/video/7709651606630944013")
    ydl = AsyncMock()

    with patch.object(tiktok_api_source, "TikTokYdlController", return_value=ydl):
        await controller._fallback_to_ydl()

    ydl.download_video.assert_awaited_once()
    await controller.close()


@pytest.mark.anyio
@pytest.mark.parametrize(("inline", "timeout"), [(True, 4), (False, 8)])
async def test_inline_request_waits_for_tikwm_less(inline: bool, timeout: int) -> None:
    """An inline answer has about ten seconds in all."""
    controller = _tiktok("https://www.tiktok.com/@nasa/video/1", inline=inline)
    controller._tikwm_is_down = AsyncMock(return_value=False)  # type: ignore[method-assign]
    controller._wait_for_slot = AsyncMock()  # type: ignore[method-assign]
    controller._note_tikwm_failure = AsyncMock()  # type: ignore[method-assign]
    post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))

    with patch.object(controller._client, "post", post):
        assert (
            await controller.get_video_info("https://www.tiktok.com/@nasa/video/1")
            is None
        )

    assert post.await_args.kwargs["timeout"] == timeout
    assert (TIKWM_INLINE_TIMEOUT, TIKWM_TIMEOUT) == (4, 8)
    await controller.close()
