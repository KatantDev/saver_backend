from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from saver_backend.services.downloaders import ok_ydl_source
from saver_backend.services.downloaders.ok_ydl_source import (
    PLAYER_ATTEMPTS,
    OkYdlController,
)
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController

URL = "https://ok.ru/video/1"
NO_PLAYER = "ERROR: [Odnoklassniki] 1: Unable to extract player; please report"

# Formats of an answer without streams, as yt-dlp returns them
PLAIN_ONLY = [
    {"format_id": name, "protocol": "https", "vcodec": None, "acodec": None}
    for name in ("mobile", "lowest", "low", "sd", "hd")
]
STREAMS = [
    {
        "format_id": f"hls-{height}",
        "protocol": "m3u8_native",
        "resolution": f"{height * 16 // 9}x{height}",
        "height": height,
        "vcodec": "avc1",
        "acodec": "mp4a",
        "tbr": 300,
    }
    for height in (144, 240)
]


def _info(formats: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": "1", "title": "t", "duration": 60, "formats": formats}


def test_answer_with_plain_files_only_still_has_a_menu() -> None:
    """Prod 23.11: «no formats» 71 times, the answer had no streams in it."""
    source = MagicMock()
    info = OkYdlController._prepare_info(source, _info(PLAIN_ONLY))  # noqa: SLF001

    video = VideoDTO.from_yt_dlp(info=info)

    assert video.unique_labels == ["720p", "480p", "360p", "240p", "144p"]
    assert video.unique_formats["360p"][0].format_id == "low"


def test_stream_is_kept_when_both_kinds_are_there() -> None:
    """With streams in the answer the menu stays as it was: one per quality."""
    source = MagicMock()
    info = OkYdlController._prepare_info(  # noqa: SLF001
        source,
        _info([*PLAIN_ONLY, *STREAMS]),
    )

    video = VideoDTO.from_yt_dlp(info=info)

    assert video.unique_formats["144p"][0].format_id == "hls-144"
    assert video.unique_formats["240p"][0].format_id == "hls-240"
    assert video.unique_formats["720p"][0].format_id == "hd"


@pytest.mark.anyio
async def test_page_without_player_is_asked_again() -> None:
    """Prod 24.11: 14 of 72 requests had no player, the next one worked."""
    source = MagicMock()
    source.SOURCE = OkYdlController.SOURCE
    answers = [DownloadError(NO_PLAYER), DownloadError(NO_PLAYER), {"id": "1"}]

    with (
        patch.object(
            YtDlpController,
            "get_video_info",
            AsyncMock(side_effect=answers),
        ) as read,
        patch.object(ok_ydl_source, "PLAYER_RETRY_PAUSE", 0),
    ):
        info = await OkYdlController.get_video_info(source, URL)

    assert info == {"id": "1"}
    assert read.await_count == 3
    assert source._switch_proxy_for_retry.call_count == 2  # noqa: SLF001


@pytest.mark.anyio
async def test_other_errors_and_the_last_attempt_are_not_hidden() -> None:
    """A different error is raised at once; the third player error too."""
    source = MagicMock()
    source.SOURCE = OkYdlController.SOURCE

    with (
        patch.object(
            YtDlpController,
            "get_video_info",
            AsyncMock(side_effect=DownloadError("ERROR: video is private")),
        ) as read,
        pytest.raises(DownloadError),
    ):
        await OkYdlController.get_video_info(source, URL)
    assert read.await_count == 1

    with (
        patch.object(
            YtDlpController,
            "get_video_info",
            AsyncMock(side_effect=DownloadError(NO_PLAYER)),
        ) as read,
        patch.object(ok_ydl_source, "PLAYER_RETRY_PAUSE", 0),
        pytest.raises(DownloadError),
    ):
        await OkYdlController.get_video_info(source, URL)
    assert read.await_count == PLAYER_ATTEMPTS


def test_download_is_retried_twice() -> None:
    """The download stage meets the same flaky answers."""
    assert OkYdlController.RETRIES_ON_ERROR == 2
