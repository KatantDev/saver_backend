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
