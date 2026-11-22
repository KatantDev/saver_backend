from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yt_dlp

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.x_ydl_source import (
    X_MAX_RESOLUTION,
    XYdlController,
)


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


def _cached_source(
    limit: int | None,
    width: int,
    height: int,
) -> tuple[MagicMock, MagicMock]:
    source = MagicMock()
    source.SOURCE = SourceEnum.X_YDL
    source.MAX_RESOLUTION = limit
    source._inline_query_id = None  # noqa: SLF001
    source._telegram_id = 1  # noqa: SLF001
    cached = MagicMock()
    cached.file_id = "cached-file-id"
    cached.meta_data_dto = VideoDTO(
        url="https://x.com/NASA/status/1",
        source_id="1",
        width=width,
        height=height,
    )
    source._cache_dao.get_by_filters = AsyncMock(return_value=cached)  # noqa: SLF001
    source._cache_dao.delete_by_file_id = AsyncMock()  # noqa: SLF001
    source._telegram_bot_controller.send_video_by_file_id = AsyncMock()  # noqa: SLF001
    source._create_history_entry = AsyncMock()  # noqa: SLF001
    source.delete_processing_message = AsyncMock()
    return source, cached


@pytest.mark.anyio
async def test_cached_4k_copy_is_dropped_and_downloaded_again() -> None:
    """A 4K file cached before the limit is not sent, the video is re-downloaded."""
    source, _ = _cached_source(X_MAX_RESOLUTION, 3840, 2160)

    sent = await BaseSourceController.send_video_from_cache(source, "1", "best")

    assert sent is False
    source._telegram_bot_controller.send_video_by_file_id.assert_not_awaited()  # noqa: SLF001
    source._cache_dao.delete_by_file_id.assert_awaited_once_with(  # noqa: SLF001
        source=SourceEnum.X_YDL,
        file_id="cached-file-id",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("limit", "width", "height"),
    [
        (X_MAX_RESOLUTION, 1920, 1080),
        (X_MAX_RESOLUTION, 1080, 1920),
        (None, 3840, 2160),
    ],
)
async def test_cached_copy_within_limit_is_sent(
    limit: int | None,
    width: int,
    height: int,
) -> None:
    """1080p copies of X and sources without a limit are served from the cache."""
    source, _ = _cached_source(limit, width, height)

    sent = await BaseSourceController.send_video_from_cache(source, "1", "best")

    assert sent is True
    source._telegram_bot_controller.send_video_by_file_id.assert_awaited_once()  # noqa: SLF001
    source._cache_dao.delete_by_file_id.assert_not_awaited()  # noqa: SLF001
