from typing import Any

import pytest
import yt_dlp

from saver_backend.services.downloaders.x_ydl_source import XYdlController


def _formats(sizes: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """Video-only HLS formats as X lists them, plus one audio track."""
    formats: list[dict[str, Any]] = [
        {
            "format_id": "hls-audio-128000",
            "url": "https://video.twimg.com/a.m3u8",
            "ext": "mp4",
            "protocol": "m3u8_native",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "tbr": 128,
        },
    ]
    for width, height in sizes:
        formats.append(
            {
                "format_id": f"hls-{width}x{height}",
                "url": f"https://video.twimg.com/{width}x{height}.m3u8",
                "ext": "mp4",
                "protocol": "m3u8_native",
                "vcodec": "avc1.640033",
                "acodec": "none",
                "width": width,
                "height": height,
                "tbr": width * height / 1000,
            },
        )
    return formats


def _selected(params: dict[str, Any], sizes: list[tuple[int, int]]) -> dict[str, Any]:
    with yt_dlp.YoutubeDL({"quiet": True, **params}) as ydl:
        info = {
            "id": "1",
            "title": "x",
            "extractor": "twitter",
            "extractor_key": "Twitter",
            "webpage_url": "https://x.com/i/status/1",
            "formats": _formats(sizes),
        }
        return ydl.process_video_result(info, download=False)


@pytest.fixture
def x_params(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Parameters the X controller puts into yt-dlp."""
    monkeypatch.setattr(
        "saver_backend.services.downloaders.ydl_source.YtDlpController.__init__",
        lambda self, *_a, **_kw: setattr(self, "_yt_dlp", yt_dlp.YoutubeDL({})),
    )
    controller = XYdlController()
    params = {
        key: controller._yt_dlp.params[key]  # noqa: SLF001
        for key in ("format", "format_sort")
    }
    controller._yt_dlp.close()  # noqa: SLF001
    return params


HORIZONTAL = [(640, 360), (1280, 720), (1920, 1080), (3840, 2160)]
VERTICAL = [(360, 640), (720, 1280), (1080, 1920), (2160, 3840)]


@pytest.mark.parametrize(
    ("sizes", "expected"),
    [(HORIZONTAL, (1920, 1080)), (VERTICAL, (1080, 1920))],
)
def test_4k_is_not_picked(
    x_params: dict[str, Any],
    sizes: list[tuple[int, int]],
    expected: tuple[int, int],
) -> None:
    """X offers 4K, the bot takes 1080p (#16), vertical videos too."""
    info = _selected(x_params, sizes)

    assert (info["width"], info["height"]) == expected
    assert info["format_id"].endswith("+hls-audio-128000")


def test_smaller_video_keeps_its_best_quality(x_params: dict[str, Any]) -> None:
    """A tweet with nothing above 720p still gets 720p."""
    info = _selected(x_params, [(640, 360), (1280, 720)])

    assert (info["width"], info["height"]) == (1280, 720)


def test_only_4k_is_still_downloaded(x_params: dict[str, Any]) -> None:
    """If X has nothing smaller, the video is sent as is rather than failing."""
    info = _selected(x_params, [(3840, 2160)])

    assert (info["width"], info["height"]) == (3840, 2160)
