import asyncio
import shutil
import subprocess
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.m3u8_ydl_source import (
    M3U8_DEFAULT_FORMAT,
    M3U8YdlController,
)

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(
    FFMPEG is None,
    reason="ffmpeg is not installed",
)

SOURCES = [
    "-f",
    "lavfi",
    "-i",
    "testsrc=size=320x240:rate=10",
    "-f",
    "lavfi",
    "-i",
    "sine=frequency=440",
    "-t",
    "2",
    "-map",
    "0:v",
    "-map",
    "1:a",
    "-c:v",
    "libx264",
    "-c:a",
    "aac",
    "-f",
    "hls",
    "-hls_time",
    "1",
    "-hls_playlist_type",
    "vod",
]


def _ffmpeg(folder: Path, *args: str) -> None:
    assert FFMPEG is not None
    subprocess.run(  # noqa: S603 - fixed arguments, no user input
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *SOURCES, *args],
        cwd=folder,
        check=True,
    )


@pytest.fixture
def streams(tmp_path: Path) -> Iterator[str]:
    """Two local HLS streams: video and audio together, and separately."""
    muxed = tmp_path / "muxed"
    split = tmp_path / "split"
    muxed.mkdir()
    split.mkdir()
    _ffmpeg(muxed, "stream.m3u8")
    _ffmpeg(
        split,
        "-master_pl_name",
        "master.m3u8",
        "-var_stream_map",
        "v:0,agroup:aud a:0,agroup:aud,default:yes",
        "-hls_segment_filename",
        "s_%v_%d.ts",
        "p_%v.m3u8",
    )
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _controller(url: str, format_id: str | None = None) -> M3U8YdlController:
    controller = M3U8YdlController(
        resolution=Resolution(source=SourceEnum.M3U8_YDL, url=url),
        telegram_bot_controller=AsyncMock(),
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=AsyncMock(),
        format_id=format_id,
    )
    controller._yt_dlp.params["proxy"] = None  # noqa: SLF001
    return controller


async def _download(controller: M3U8YdlController, url: str) -> Path:
    info = await controller.get_video_info(url)
    assert info is not None
    await asyncio.to_thread(controller._yt_dlp.process_info, info)  # noqa: SLF001
    assert controller._video is not None  # noqa: SLF001
    return Path(controller._video.path)  # noqa: SLF001


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["muxed/stream.m3u8", "split/master.m3u8"])
async def test_stream_is_saved_where_the_bot_looks_for_it(
    streams: str,
    path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The downloaded file is found after download (it was «Error» every time)."""
    monkeypatch.chdir(tmp_path)
    url = f"{streams}/{path}"
    controller = _controller(url)

    file_path = await _download(controller, url)

    assert file_path.exists()
    assert file_path.stat().st_size > 0
    await controller.close()


@pytest.mark.anyio
async def test_chosen_quality_is_kept() -> None:
    """A quality picked in the menu is not replaced by the default format."""
    picked = _controller("https://example.com/a.m3u8", format_id="720")
    default = _controller("https://example.com/a.m3u8")

    assert picked._yt_dlp.params["format"] == "720"  # noqa: SLF001
    assert default._yt_dlp.params["format"] == M3U8_DEFAULT_FORMAT  # noqa: SLF001
