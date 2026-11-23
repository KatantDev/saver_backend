# ruff: noqa: SLF001
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from yt_dlp.utils import DownloadError

from saver_backend.services.consts import MAX_FILE_SIZE_BYTES
from saver_backend.services.downloaders.youtube_video_ydl_source import (
    MAX_PROXY_SWITCHES,
    YouTubeVideoYdlController,
    is_bot_check,
    proxy_key,
)

BOT_CHECK = DownloadError(
    "ERROR: [youtube] RpT8-vgzkS0: Sign in to confirm you’re not a bot.",
)
MB = 1024 * 1024


def _controller(proxies: list[str], blocked: dict[str, float] | None = None) -> Any:
    controller: Any = YouTubeVideoYdlController.__new__(YouTubeVideoYdlController)
    controller._proxy, *controller._proxies = proxies or [None]
    controller._yt_dlp = MagicMock(params={})
    controller._telegram_bot_controller = MagicMock()
    controller._telegram_bot_controller.get_fsm_data = AsyncMock(
        return_value=blocked or {},
    )
    controller._telegram_bot_controller.update_fsm_data = AsyncMock()

    def set_proxy() -> None:
        controller._proxy = controller._proxies.pop(0)

    def go_direct() -> None:
        controller._proxy = ""
        controller._proxies = []

    controller._set_proxy = set_proxy
    controller._switch_to_direct = go_direct
    return controller


def test_bot_check_is_recognised() -> None:
    """The answer YouTube gives through a flagged address."""
    assert is_bot_check(BOT_CHECK)
    assert not is_bot_check(DownloadError("HTTP Error 403: Forbidden"))


@pytest.mark.anyio
async def test_flagged_proxy_is_remembered_without_its_address() -> None:
    """Prod 23.11: 4 of 12 proxies got the bot check for every video."""
    controller = _controller(["socks5://u:p@1.1.1.1:1", "socks5://u:p@2.2.2.2:1"])

    await controller._note_bot_check(BOT_CHECK)

    call = controller._telegram_bot_controller.update_fsm_data.await_args
    ((key, until),) = call.kwargs["data"].items()
    assert key == proxy_key("socks5://u:p@1.1.1.1:1")
    assert "1.1.1.1" not in key
    assert until > time.time()


@pytest.mark.anyio
async def test_other_errors_do_not_flag_the_proxy() -> None:
    """A 403 on one video says nothing about the proxy as a whole."""
    controller = _controller(["socks5://u:p@1.1.1.1:1"])

    await controller._note_bot_check(DownloadError("HTTP Error 403: Forbidden"))

    controller._telegram_bot_controller.update_fsm_data.assert_not_awaited()


@pytest.mark.anyio
async def test_request_starts_from_a_proxy_that_is_not_flagged() -> None:
    """The request does not spend its tries on proxies known to be refused."""
    a, b, c = "socks5://a:1", "socks5://b:1", "socks5://c:1"
    future = time.time() + 600
    controller = _controller([a, b, c], {proxy_key(a): future, proxy_key(c): future})

    await controller._prepare_routes()

    assert controller._proxy == b
    assert controller._proxies == []


@pytest.mark.anyio
async def test_old_flags_are_forgotten() -> None:
    """A proxy is tried again once its pause is over."""
    a, b = "socks5://a:1", "socks5://b:1"
    controller = _controller([a, b], {proxy_key(a): time.time() - 1})

    await controller._prepare_routes()

    assert controller._proxy == a
    assert controller._proxies == [b]


@pytest.mark.anyio
async def test_all_proxies_flagged_goes_from_the_server() -> None:
    """With every proxy flagged the server's own address is used at once."""
    a = "socks5://a:1"
    controller = _controller([a], {proxy_key(a): time.time() + 600})

    await controller._prepare_routes()

    assert controller._proxy == ""


def test_routes_go_through_two_proxies_then_the_server() -> None:
    """A request tries a few proxies, then the server, then stops."""
    controller = _controller([f"socks5://p{n}:1" for n in range(6)])

    steps = []
    while controller._next_route():
        steps.append(controller._proxy)

    assert steps == ["socks5://p1:1", "socks5://p2:1", ""]
    assert len(steps) == MAX_PROXY_SWITCHES + 1
    assert controller._next_route() is False


def test_menu_size_counts_the_audio_and_original_track_wins() -> None:
    """The file sent is video plus audio; the levelled «-drc» track is not used."""
    controller = _controller(["socks5://a:1"])
    info = {
        "formats": [
            {
                "format_id": "137",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "video_ext": "mp4",
                "filesize": 1900 * MB,
            },
            {
                "format_id": "140-drc",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "video_ext": "none",
                "audio_ext": "m4a",
                "abr": 129.5,
                "filesize": 140 * MB,
            },
            {
                "format_id": "140",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "video_ext": "none",
                "audio_ext": "m4a",
                "abr": 129.5,
                "filesize": 140 * MB,
            },
        ],
    }

    edited = controller._edit_format_info_dict(info)

    merged = edited["formats"][0]
    assert merged["format_id"] == "137+140"
    assert merged["filesize"] == 2040 * MB
    assert merged["filesize"] > MAX_FILE_SIZE_BYTES


def test_limit_is_telegrams_two_gigabytes() -> None:
    """Prod 21-23.11: 93 files between 2 and 3 GB got FILE_PARTS_INVALID."""
    assert MAX_FILE_SIZE_BYTES < 2000 * MB
