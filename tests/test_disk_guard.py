import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from saver_backend.services.cleanup.clear_old import CleanupService
from saver_backend.services.consts import TELEGRAM_UPLOAD_LIMIT_BYTES
from saver_backend.services.downloaders.exceptions import FileTooLargeError
from saver_backend.services.downloaders.ydl_source import YtDlpController

MB = 1024 * 1024
HOUR = 3600


def _source(tmp_path: Path) -> MagicMock:
    source = MagicMock()
    source._bytes_by_file = {}  # noqa: SLF001
    source._download_directory = tmp_path  # noqa: SLF001
    source._download_token = "abcd1234"  # noqa: SLF001
    return source


def test_plain_file_is_stopped_at_the_first_report(tmp_path: Path) -> None:
    """Prod 23.11: a 3.9 GB VK file was downloaded whole before the refusal."""
    source = _source(tmp_path)
    report = {"filename": "v.mp4", "total_bytes": 3859 * MB, "downloaded_bytes": 1024}

    with pytest.raises(FileTooLargeError):
        YtDlpController._check_size(source, report)  # noqa: SLF001


def test_file_under_the_limit_goes_on(tmp_path: Path) -> None:
    """A usual file is not touched, and a rough estimate is not trusted."""
    source = _source(tmp_path)
    report = {
        "filename": "v.mp4",
        "total_bytes": 65 * MB,
        "total_bytes_estimate": 5000 * MB,
        "downloaded_bytes": 4 * MB,
    }

    YtDlpController._check_size(source, report)  # noqa: SLF001


def test_stream_is_stopped_when_it_outgrows_the_limit(tmp_path: Path) -> None:
    """A stream has no exact size: the downloaded bytes are counted."""
    source = _source(tmp_path)
    under = {"filename": "v.mp4", "downloaded_bytes": TELEGRAM_UPLOAD_LIMIT_BYTES - 1}
    over = {"filename": "v.mp4", "downloaded_bytes": TELEGRAM_UPLOAD_LIMIT_BYTES + 1}

    YtDlpController._check_size(source, under)  # noqa: SLF001
    with pytest.raises(FileTooLargeError):
        YtDlpController._check_size(source, over)  # noqa: SLF001


def test_video_and_audio_files_are_summed(tmp_path: Path) -> None:
    """Separate video and audio streams end up in one file."""
    source = _source(tmp_path)
    video = {"filename": "v.f137.mp4", "downloaded_bytes": 1500 * MB}
    audio = {"filename": "v.f140.m4a", "downloaded_bytes": 600 * MB}

    YtDlpController._check_size(source, video)  # noqa: SLF001
    with pytest.raises(FileTooLargeError):
        YtDlpController._check_size(source, audio)  # noqa: SLF001


def test_only_own_partial_files_are_removed(tmp_path: Path) -> None:
    """A stopped download deletes its files and nobody else's."""
    source = _source(tmp_path)
    own = [
        tmp_path / "-1_2.abcd1234.mp4.part",
        tmp_path / "-1_2.abcd1234.mp4.part-Frag7.part",
        tmp_path / "-1_2.abcd1234.mp4.ytdl",
    ]
    other = tmp_path / "-1_2.ffff0000.mp4.part"
    for path in [*own, other]:
        path.write_bytes(b"x")

    YtDlpController._remove_partial_files(source)  # noqa: SLF001

    assert not any(path.exists() for path in own)
    assert other.exists()


@pytest.mark.anyio
async def test_too_large_download_answers_file_too_large(tmp_path: Path) -> None:
    """The user gets «file too large», not the general error, and no leftovers."""
    source = _source(tmp_path)
    source._telegram_id = 1  # noqa: SLF001
    source._message_id = 10  # noqa: SLF001
    source._yt_dlp.process_info = MagicMock(  # noqa: SLF001
        side_effect=FileTooLargeError(3859 * MB),
    )
    source._telegram_bot_controller.send_file_too_large = AsyncMock()  # noqa: SLF001
    source._send_error_message = AsyncMock()  # noqa: SLF001
    source._send_video = AsyncMock()  # noqa: SLF001

    await YtDlpController._execute_download(source, {"id": "-1_2"})  # noqa: SLF001

    source._telegram_bot_controller.send_file_too_large.assert_awaited_once_with(  # noqa: SLF001
        telegram_id=1,
        message_id=10,
    )
    source._remove_partial_files.assert_called_once()  # noqa: SLF001
    source._send_error_message.assert_not_awaited()  # noqa: SLF001
    source._send_video.assert_not_awaited()  # noqa: SLF001


def _aged(path: Path, hours: float) -> Path:
    path.write_bytes(b"x")
    stamp = time.time() - hours * HOUR
    os.utime(path, (stamp, stamp))
    return path


@pytest.mark.anyio
async def test_abandoned_partial_files_go_after_two_hours(tmp_path: Path) -> None:
    """Prod 24.11: 22 GB of .part files waited for the 72-hour cleanup."""
    folder = tmp_path / "vk_video_ydl"
    folder.mkdir()
    old_part = _aged(folder / "-1_2.aaaa0000.mp4.part", 3)
    old_fragment = _aged(folder / "-1_2.aaaa0000.mp4.part-Frag9.part", 3)
    old_state = _aged(folder / "-1_2.aaaa0000.mp4.ytdl", 3)
    running_part = _aged(folder / "-3_4.bbbb0000.mp4.part", 0.5)
    finished_file = _aged(folder / "-5_6.cccc0000.mp4", 3)
    very_old_file = _aged(folder / "-7_8.dddd0000.mp4", 80)

    await CleanupService(download_path=tmp_path).cleanup()

    assert not old_part.exists()
    assert not old_fragment.exists()
    assert not old_state.exists()
    assert running_part.exists()
    assert finished_file.exists()
    assert not very_old_file.exists()
