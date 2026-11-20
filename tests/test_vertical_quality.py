from typing import Any

import pytest
import yt_dlp

from saver_backend.services.downloaders.dzen_ydl import DzenYdlController
from saver_backend.services.downloaders.facebook_ydl_source import (
    FacebookYdlController,
)
from saver_backend.services.downloaders.ydl_source import YtDlpController


def _formats(sizes: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """DASH video-only streams in the order Facebook lists them, plus audio."""
    formats: list[dict[str, Any]] = [
        {
            "format_id": "audio",
            "url": "https://example.com/audio.m4a",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a.40.5",
            "tbr": 69,
        },
        # progressive links without a known size come first on Facebook
        {"format_id": "sd", "url": "https://example.com/sd.mp4", "ext": "mp4"},
        {"format_id": "hd", "url": "https://example.com/hd.mp4", "ext": "mp4"},
    ]
    for width, height in sizes:
        formats.append(
            {
                "format_id": f"v{width}x{height}",
                "url": f"https://example.com/{width}x{height}.mp4",
                "ext": "mp4",
                "vcodec": "av01.0.05M.08",
                "acodec": "none",
                "width": width,
                "height": height,
                "tbr": width * height / 1000,
            },
        )
    return formats


def _params(
    controller_class: type[YtDlpController],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    monkeypatch.setattr(
        YtDlpController,
        "__init__",
        lambda self, *_a, **_kw: setattr(self, "_yt_dlp", yt_dlp.YoutubeDL({})),
    )
    controller = controller_class()
    params = {
        key: controller._yt_dlp.params[key]  # noqa: SLF001
        for key in ("format", "format_sort")
    }
    controller._yt_dlp.close()  # noqa: SLF001
    return params


def _selected(params: dict[str, Any], sizes: list[tuple[int, int]]) -> tuple[int, int]:
    with yt_dlp.YoutubeDL({"quiet": True, **params}) as ydl:
        info = ydl.process_video_result(
            {
                "id": "1",
                "title": "reel",
                "extractor": "generic",
                "extractor_key": "Generic",
                "webpage_url": "https://example.com/reel/1",
                "formats": _formats(sizes),
            },
            download=False,
        )
    return info["width"], info["height"]


CONTROLLERS = [FacebookYdlController, DzenYdlController]
VERTICAL = [(540, 960), (720, 1280), (1080, 1920)]


@pytest.mark.parametrize("controller_class", CONTROLLERS)
def test_vertical_video_keeps_its_resolution(
    controller_class: type[YtDlpController],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FB-02 and DZ-02: a 1080x1920 reel used to come as 540x960 or 480x852."""
    params = _params(controller_class, monkeypatch)

    assert _selected(params, VERTICAL) == (1080, 1920)


@pytest.mark.parametrize("controller_class", CONTROLLERS)
def test_4k_is_still_capped(
    controller_class: type[YtDlpController],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap stays at 1080p on the smaller side, for both orientations."""
    params = _params(controller_class, monkeypatch)

    assert _selected(params, [*VERTICAL, (2160, 3840)]) == (1080, 1920)
    assert _selected(params, [(1920, 1080), (3840, 2160)]) == (1920, 1080)


def test_dzen_retries_through_other_proxies() -> None:
    """Prod 20.11: 2 of 3 proxies timed out on Yandex SSO, the third worked."""
    assert DzenYdlController.RETRIES_ON_ERROR == 3
