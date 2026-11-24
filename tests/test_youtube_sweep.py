# ruff: noqa: SLF001
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError

from saver_backend.services.downloaders import ydl_source
from saver_backend.services.downloaders.schema import VideoDTO, is_live_stream
from saver_backend.services.downloaders.ydl_source import (
    MAX_CONNECTION_RETRIES,
    YtDlpController,
    is_connection_failure,
)
from saver_backend.services.downloaders.youtube_shorts_ydl_source import (
    YouTubeShortsYdlController,
)
from saver_backend.services.downloaders.youtube_video_ydl_source import (
    YouTubeVideoYdlController,
    download_format_chain,
    is_age_restricted,
    is_bot_check,
    is_gone,
    is_region_refusal,
)

GEO = DownloadError(
    "ERROR: [youtube] afedl8HylCY: The uploader has not made this video "
    "available in your country",
)
CLAIMED = DownloadError(
    "ERROR: [youtube] LR5Q8vgnk6M: Video unavailable. It was blocked due to "
    "the claimed content by WMG.",
)
AGE = DownloadError(
    "ERROR: [youtube] x: Sign in to confirm your age. This video may be "
    "inappropriate for some users.",
)
PRIVATE = DownloadError(
    "ERROR: [youtube] x: Private video. Sign in if you've been granted access",
)
TIMEOUT = DownloadError(
    "ERROR: [vk] x: Unable to download webpage: SocksHTTPSConnectionPool: "
    "Read timed out. (read timeout=20.0)",
)


def _video(height: int, format_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "format_id": format_id,
        "ext": "mp4",
        "vcodec": "avc1.640028",
        "acodec": "none",
        "height": height,
        "width": height * 16 // 9,
        "protocol": "https",
        "url": f"https://v/{format_id}",
        **extra,
    }


def _audio(format_id: str) -> dict[str, Any]:
    return {
        "format_id": format_id,
        "ext": "m4a",
        "vcodec": "none",
        "acodec": "mp4a.40.2",
        "abr": 128,
        "protocol": "https",
        "url": f"https://a/{format_id}",
    }


def _combined(height: int, format_id: str) -> dict[str, Any]:
    return {
        "format_id": format_id,
        "ext": "mp4",
        "vcodec": "avc1.640028",
        "acodec": "mp4a.40.2",
        "height": height,
        "width": height * 16 // 9,
        "protocol": "m3u8_native",
        "url": f"https://c/{format_id}",
    }


def _picked(spec: str, formats: list[dict[str, Any]]) -> list[str]:
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        selector = ydl.build_format_selector(spec)
        chosen = list(
            selector({"formats": formats, "incomplete_formats": False}),
        )
    return [chosen[0]["format_id"]] if chosen else []


# yt-dlp sorts formats from worst to best: the original audio track is last
DEFAULT_CLIENT = [
    _audio("140-1"),
    _audio("140-0"),
    _video(360, "134"),
    _video(720, "136"),
    _video(1080, "137"),
]
EMBEDDED_CLIENT = [_combined(360, "93"), _combined(720, "95"), _combined(1080, "96")]
DUBBED_EMBEDDED = [_combined(720, "95-0"), _combined(1080, "96-0")]


def test_picked_quality_is_downloaded_as_is_when_it_is_there() -> None:
    """Nothing changes for the usual case."""
    assert _picked(download_format_chain("137+140-0"), DEFAULT_CLIENT) == ["137+140-0"]


def test_other_audio_id_does_not_fail_the_download() -> None:
    """«133+140-18»: the suffix of a dubbed video differs between requests."""
    formats = [_audio("140"), _video(1080, "137")]
    assert _picked(download_format_chain("137+140-18"), formats) == ["137+140"]


def test_same_quality_is_found_in_the_other_client() -> None:
    """Prod 23.11: after a 403 the retry had only combined files (66 failures)."""
    assert _picked(download_format_chain("137+140-0"), EMBEDDED_CLIENT) == ["96"]
    assert _picked(download_format_chain("136+140"), DUBBED_EMBEDDED) == ["95-0"]


def test_menu_of_the_other_client_downloads_through_the_default_one() -> None:
    """A menu built from combined files («96», «95-0») and a default download."""
    assert _picked(download_format_chain("96"), DEFAULT_CLIENT) == ["137+140-0"]
    assert _picked(download_format_chain("95-0"), DEFAULT_CLIENT) == ["136+140-0"]
    assert _picked(download_format_chain("96"), EMBEDDED_CLIENT) == ["96"]


def test_last_resort_does_not_go_above_the_picked_quality() -> None:
    """Without the picked picture the closest lower H.264 one is taken."""
    formats = [_audio("140"), _video(360, "134"), _video(1080, "137")]
    assert _picked(download_format_chain("136+140"), formats) == ["134+140"]


def test_unknown_ids_are_left_alone() -> None:
    """Formats outside the table keep the old behaviour."""
    assert download_format_chain("399+251") == "399+251"
    assert download_format_chain("18") == "18"


def test_refusals_are_told_apart() -> None:
    """Country, age, gone and bot check lead to different actions."""
    assert is_region_refusal(GEO)
    assert is_region_refusal(CLAIMED)
    assert not is_region_refusal(AGE)
    assert is_gone(CLAIMED)
    assert is_gone(PRIVATE)
    assert not is_gone(GEO)
    assert not is_gone(
        DownloadError(
            "Video unavailable. This content isn't available, try again later"
        ),
    )
    assert is_age_restricted(AGE)
    assert not is_bot_check(AGE)
    assert is_bot_check(DownloadError("Sign in to confirm you’re not a bot"))


def _youtube(cls: Any, proxy: str | None) -> Any:
    controller: Any = cls.__new__(cls)
    controller._proxy = proxy
    controller._proxies = []
    controller._selected_format_id = None
    controller._routes_ready = True
    controller._cookie_fallback = False
    controller._base_options = {}
    controller._connection_retries = 0
    controller._telegram_id = 1
    controller._resolution = MagicMock(url="https://youtu.be/afedl8HylCY")
    controller._telegram_bot_controller = MagicMock()
    controller._telegram_bot_controller.send_content_not_found_error = AsyncMock()
    controller._telegram_bot_controller.send_age_restricted_error = AsyncMock()
    controller._telegram_bot_controller.update_fsm_data = AsyncMock()
    controller.delete_processing_message = AsyncMock()
    controller._yt_dlp = MagicMock(params={})
    controller._download_directory = Path("/tmp")  # noqa: S108
    controller._download_token = "t"

    def go_direct() -> None:
        controller._proxy = ""

    controller._switch_to_direct = go_direct
    return controller


@pytest.mark.anyio
async def test_country_block_through_a_proxy_is_retried_from_the_server() -> None:
    """A video blocked for the proxy's country opens from the server."""
    controller = _youtube(YouTubeVideoYdlController, "socks5://ru")
    info = {"id": "afedl8HylCY", "ext": "mp4", "formats": []}
    controller._yt_dlp.extract_info = MagicMock(side_effect=[GEO, info])

    result = await controller.get_video_info("https://youtu.be/afedl8HylCY")

    assert result == info
    assert controller._proxy == ""
    controller._telegram_bot_controller.update_fsm_data.assert_not_awaited()


@pytest.mark.anyio
async def test_country_block_from_the_server_is_an_error() -> None:
    """Blocked in the server's country too: the error goes up as before."""
    controller = _youtube(YouTubeVideoYdlController, "")
    controller._yt_dlp.extract_info = MagicMock(side_effect=GEO)

    with pytest.raises(DownloadError):
        await controller.get_video_info("https://youtu.be/afedl8HylCY")

    assert controller._yt_dlp.extract_info.call_count == 1


@pytest.mark.anyio
async def test_private_video_is_answered_private_or_deleted() -> None:
    """A closed video gets its own answer instead of the general error."""
    controller = _youtube(YouTubeVideoYdlController, "")
    controller._yt_dlp.extract_info = MagicMock(side_effect=PRIVATE)

    result = await controller.get_video_info("https://youtu.be/x")

    assert result is None
    send = controller._telegram_bot_controller.send_content_not_found_error
    send.assert_awaited_once()


@pytest.mark.anyio
async def test_age_restricted_video_is_named_and_flags_no_proxy() -> None:
    """Without cookies the user is told why; the proxy is not marked as flagged."""
    controller = _youtube(YouTubeVideoYdlController, "socks5://nl")
    controller._yt_dlp.extract_info = MagicMock(side_effect=AGE)

    with patch.object(controller, "_has_cookie_files", return_value=False):
        result = await controller.get_video_info("https://youtu.be/x")

    assert result is None
    controller._telegram_bot_controller.send_age_restricted_error.assert_awaited_once()
    controller._telegram_bot_controller.update_fsm_data.assert_not_awaited()
    assert controller._yt_dlp.extract_info.call_count == 1


@pytest.mark.anyio
async def test_short_blocked_for_the_proxy_is_retried_from_the_server() -> None:
    """Prod: 6 of 8 shorts answered «not found» opened from the server."""
    controller = _youtube(YouTubeShortsYdlController, "socks5://ru")
    info = {"id": "LR5Q8vgnk6M", "ext": "mp4", "formats": []}
    base = AsyncMock(side_effect=[CLAIMED, info])

    with patch.object(YtDlpController, "get_video_info", base):
        result = await controller.get_video_info("https://youtube.com/shorts/x")

    assert result == info
    send = controller._telegram_bot_controller.send_content_not_found_error
    send.assert_not_awaited()


@pytest.mark.anyio
async def test_short_gone_everywhere_is_not_found() -> None:
    """Blocked from the server too: «private or deleted»."""
    controller = _youtube(YouTubeShortsYdlController, "socks5://ru")
    base = AsyncMock(side_effect=[CLAIMED, CLAIMED])

    with patch.object(YtDlpController, "get_video_info", base):
        result = await controller.get_video_info("https://youtube.com/shorts/x")

    assert result is None
    assert base.await_count == 2
    send = controller._telegram_bot_controller.send_content_not_found_error
    send.assert_awaited_once()


def test_client_is_kept_while_a_menu_quality_is_downloaded() -> None:
    """The retry of a picked quality stays on the client that built the menu."""
    controller = _youtube(YouTubeVideoYdlController, "socks5://a")
    controller._proxies = ["socks5://b"]
    controller._set_proxy = MagicMock()
    controller._next_player_client = MagicMock()

    controller._selected_format_id = "137+140-0"
    controller._switch_proxy_for_retry()
    controller._next_player_client.assert_not_called()

    controller._selected_format_id = None
    controller._switch_proxy_for_retry()
    controller._next_player_client.assert_called_once()


def test_finished_broadcast_is_not_live() -> None:
    """«post_live»: the stream is over and its recording downloads."""
    assert not is_live_stream({"live_status": "post_live"})
    assert is_live_stream({"live_status": "is_live"})
    assert is_live_stream({"live_status": "is_upcoming"})


def test_label_is_dropped_when_the_file_came_under_another_id() -> None:
    """No empty «[]» in the caption after a fallback format."""
    video = VideoDTO(source_id="1", url="https://youtu.be/x", title="clip")
    video.quality = "137+140-0"
    with patch.object(
        VideoDTO,
        "unique_formats",
        new_callable=lambda: property(lambda _self: {"1080p": []}),
    ):
        assert "[]" not in video.title_html


class _Plain(YtDlpController):
    async def close(self) -> None:
        return None


def _plain(proxies: list[str]) -> Any:
    controller: Any = _Plain.__new__(_Plain)
    controller._proxy, *controller._proxies = proxies
    controller._connection_retries = 0
    controller._selected_format_id = None
    controller._download_directory = MagicMock()
    controller._download_token = "t"
    controller._yt_dlp = MagicMock(params={})

    def set_proxy() -> None:
        controller._proxy = controller._proxies.pop(0)

    controller._set_proxy = set_proxy
    return controller


@pytest.mark.anyio
async def test_dead_proxy_is_replaced_at_the_menu_stage() -> None:
    """A timeout through one proxy no longer ends the request (45 in 3 days)."""
    controller = _plain(["socks5://dead", "socks5://ok"])
    info = {"id": "1", "ext": "mp4", "formats": []}
    controller._yt_dlp.extract_info = MagicMock(side_effect=[TIMEOUT, info])

    with patch.object(ydl_source.VideoDTO, "from_yt_dlp", return_value=MagicMock()):
        result = await controller.get_video_info("https://vk.com/video1_1")

    assert result == info
    assert controller._proxy == "socks5://ok"


@pytest.mark.anyio
async def test_timeouts_are_retried_a_limited_number_of_times() -> None:
    """After the allowed switches the timeout is passed on."""
    proxies = [f"socks5://p{n}" for n in range(6)]
    controller = _plain(proxies)
    controller._yt_dlp.extract_info = MagicMock(side_effect=TIMEOUT)

    with pytest.raises(DownloadError):
        await controller.get_video_info("https://vk.com/video1_1")

    assert controller._yt_dlp.extract_info.call_count == MAX_CONNECTION_RETRIES + 1
    assert is_connection_failure(TIMEOUT)
    assert not is_connection_failure(GEO)
