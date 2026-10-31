import shutil
from pathlib import Path

import ffmpeg
import pytest

from saver_backend.services.telegram.bot_controller import _probe_video_size

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)


@needs_ffmpeg
@pytest.mark.anyio
async def test_reads_frame_size(tmp_path: Path) -> None:
    """The frame size of a vertical video is read as width x height."""
    video = tmp_path / "v.mp4"
    (
        ffmpeg.input("color=c=black:s=576x1024:d=1", f="lavfi")
        .output(str(video), pix_fmt="yuv420p")
        .run(quiet=True)
    )

    assert await _probe_video_size(str(video)) == (576, 1024)


@needs_ffmpeg
@pytest.mark.anyio
async def test_returns_none_for_unreadable_input(tmp_path: Path) -> None:
    """A file that is not a video gives no size instead of an error."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")

    assert await _probe_video_size(str(broken)) is None


@pytest.mark.anyio
async def test_downloads_file_to_temp_path(tmp_path: Path) -> None:
    """The helper saves the response body to a temporary file."""
    from aiohttp import web

    from saver_backend.services.telegram.bot_controller import (
        _download_to_temp_file,
    )

    async def handler(_request: web.Request) -> web.Response:
        return web.Response(body=b"x" * 3_000_000)

    app = web.Application()
    app.router.add_get("/v.mp4", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        path = await _download_to_temp_file(f"http://127.0.0.1:{port}/v.mp4")
        try:
            assert path.stat().st_size == 3_000_000
        finally:
            path.unlink()
    finally:
        await runner.cleanup()
