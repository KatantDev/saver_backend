from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from saver_backend.services.downloaders import ydl_source
from saver_backend.services.downloaders.ydl_source import YtDlpController
from saver_backend.services.downloaders.youtube_video_ydl_source import (
    YouTubeVideoYdlController,
)


def _video(format_id: str, height: int, vcodec: str = "avc1.640028") -> dict[str, Any]:
    return {
        "format_id": format_id,
        "height": height,
        "width": height * 16 // 9,
        "ext": "mp4",
        "video_ext": "mp4",
        "audio_ext": "none",
        "vcodec": vcodec,
        "acodec": "none",
    }


def _formats(*, with_773: bool) -> list[dict[str, Any]]:
    formats = [
        {
            "format_id": "140",
            "ext": "m4a",
            "video_ext": "none",
            "audio_ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "abr": 129,
        },
        {
            "format_id": "18",
            "height": 360,
            "ext": "mp4",
            "video_ext": "mp4",
            "audio_ext": "none",
            "vcodec": "avc1.42001E",
            "acodec": "mp4a.40.2",
        },
        _video("137", 1080),
        _video("136", 720),
        _video("401", 2160, vcodec="av01.0.12M.08"),
    ]
    if with_773:
        # 6-channel audio track YouTube added; yt-dlp leaves its codecs empty
        formats.append(
            {
                "format_id": "773",
                "ext": "m4a",
                "video_ext": "m4a",
                "audio_ext": "none",
                "vcodec": None,
                "acodec": None,
                "audio_channels": 6,
            },
        )
    return formats


def _merged_ids(formats: list[dict[str, Any]]) -> list[str]:
    controller = MagicMock()
    controller._remove_combined_formats = (  # noqa: SLF001
        lambda f: YouTubeVideoYdlController._remove_combined_formats(  # noqa: SLF001
            controller, f
        )
    )
    info = YouTubeVideoYdlController._edit_format_info_dict(  # noqa: SLF001
        controller, {"formats": formats}
    )
    return [f["format_id"] for f in info["formats"] if "+" in f["format_id"]]


def test_format_773_does_not_cut_the_menu_to_360p() -> None:
    """aqz-KE-bpKQ (4K) offered only 360p because of format 773."""
    assert _merged_ids(_formats(with_773=True)) == ["137+140", "136+140"]


def test_menu_without_773_is_unchanged() -> None:
    """Videos without the new track keep the same H.264 qualities."""
    assert _merged_ids(_formats(with_773=False)) == ["137+140", "136+140"]


def _retrying_controller(
    proxies: list[str],
) -> tuple[YouTubeVideoYdlController, list[str]]:
    controller = YouTubeVideoYdlController.__new__(YouTubeVideoYdlController)
    controller._proxy, *rest = proxies  # noqa: SLF001
    controller._proxies = rest  # noqa: SLF001
    controller._resolution = MagicMock(url="https://youtu.be/x", metadata={"code": "x"})  # noqa: SLF001
    controller._selected_format_id = "137+140"  # noqa: SLF001
    controller._yt_dlp = MagicMock(params={"proxy": controller._proxy})  # noqa: SLF001
    controller.send_video_from_cache = AsyncMock(return_value=False)
    used: list[str] = []

    async def fetch(_quality: str, *, raise_errors: bool) -> None:
        used.append(controller._proxy or "direct")  # noqa: SLF001
        if controller._proxy:  # noqa: SLF001
            raise DownloadError("HTTP Error 403: Forbidden")

    controller._fetch_and_send = fetch  # type: ignore[method-assign]  # noqa: SLF001
    controller._create_yt_dlp = lambda params: MagicMock(params=params)  # type: ignore[method-assign]  # noqa: SLF001
    return controller, used


@pytest.mark.anyio
async def test_blocked_proxies_fall_back_to_direct() -> None:
    """Dev: both proxies got 403 while the server itself could download."""
    controller, used = _retrying_controller(["proxy-a", "proxy-b"])

    with patch.object(ydl_source, "RETRY_PAUSE_SECONDS", 0):
        await YtDlpController.download_video(controller)

    assert used == ["proxy-a", "proxy-b", "direct"]


@pytest.mark.anyio
async def test_without_proxies_there_is_no_extra_attempt() -> None:
    """A source without proxies keeps its usual number of attempts."""
    controller, _used = _retrying_controller([""])

    with (
        patch.object(ydl_source, "RETRY_PAUSE_SECONDS", 0),
        pytest.raises(DownloadError),
    ):
        controller._fetch_and_send = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
            side_effect=DownloadError("boom")
        )
        await YtDlpController.download_video(controller)

    assert controller._fetch_and_send.await_count == 2  # noqa: SLF001


def test_first_try_uses_the_default_client() -> None:
    """Prod 23.11: tv_simply returns only 360p, the default client up to 1080p."""
    from saver_backend.services.downloaders.youtube_video_ydl_source import (
        YOUTUBE_PLAYER_CLIENTS,
        youtube_extractor_args,
    )

    args = youtube_extractor_args(YOUTUBE_PLAYER_CLIENTS[0])

    assert args["youtube"]["player_client"] == ["default"]
    assert "youtubepot-bgutilhttp" in args
    assert all("tv_simply" not in clients for clients in YOUTUBE_PLAYER_CLIENTS)


def test_each_retry_switches_the_player_client() -> None:
    """A retry goes through another proxy and the other client."""
    controller = YouTubeVideoYdlController.__new__(YouTubeVideoYdlController)
    controller._yt_dlp = MagicMock(params={})  # noqa: SLF001

    with patch.object(YtDlpController, "_switch_proxy_for_retry"):
        controller._switch_proxy_for_retry()  # noqa: SLF001
        first = controller._yt_dlp.params["extractor_args"]["youtube"]  # noqa: SLF001
        controller._switch_proxy_for_retry()  # noqa: SLF001
        second = controller._yt_dlp.params["extractor_args"]["youtube"]  # noqa: SLF001

    assert first["player_client"] == ["web_embedded"]
    assert second["player_client"] == ["default"]
