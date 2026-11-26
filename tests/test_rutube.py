from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from saver_backend.services.downloaders.rutube_ydl_source import (
    INFO_ATTEMPTS,
    RutubeYdlController,
    mirrors_of,
)
from saver_backend.services.downloaders.ydl_source import YtDlpController

URL = "https://rutube.ru/video/38cadd3199509ce2519edf58244554b1/"
CDN = "https://river-m9-pitix-d3105.rtbcdn.ru/a.mp4.m3u8"
RIVER = "https://river-1.rutube.ru/a.mp4.m3u8"
NO_FORMATS = "ERROR: [rutube] 38cadd: No video formats found!; please report"
DELETED = (
    "ERROR: [rutube] 8b2cd6: Unable to download video JSON: HTTP Error 403:"
    " Forbidden (caused by <HTTPError 403: Forbidden>)"
)

# The formats of one video as yt-dlp lists them: two balancers, two servers
FORMATS = [
    {"format_id": f"{balancer}-{tbr}-{n}", "height": height, "url": url}
    for tbr, height in ((911, 360), (2291, 720))
    for balancer in ("default", "m3u8")
    for n, url in enumerate((CDN, RIVER))
]


def _info(picked: str, formats: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"id": "1", "format_id": picked, "formats": formats or FORMATS}


def test_same_quality_on_the_other_server_is_found() -> None:
    """Prod 24.11: river-1 did not answer, the CDN copy of the quality did."""
    assert mirrors_of(_info("m3u8-2291-1")) == ["m3u8-2291-0", "default-2291-0"]
    assert mirrors_of(_info("default-911-0")) == ["default-911-1", "m3u8-911-1"]


def test_no_mirrors_when_there_is_nothing_to_compare() -> None:
    """One server, an unknown format or no height: the retry keeps the format."""
    one_server = [f for f in FORMATS if f["url"] == RIVER]
    assert mirrors_of(_info("m3u8-2291-1", one_server)) == []
    assert mirrors_of(_info("best")) == []
    assert mirrors_of({"id": "1"}) == []
    no_height = [{"format_id": "a", "url": CDN}, {"format_id": "b", "url": RIVER}]
    assert mirrors_of(_info("a", no_height)) == []


def test_retry_downloads_from_the_other_server() -> None:
    """The next try asks yt-dlp for the mirror first, the picked format last."""
    source = MagicMock()
    source.SOURCE = RutubeYdlController.SOURCE
    source._selected_format_id = "m3u8-2291-1"  # noqa: SLF001
    source._yt_dlp.params = {"format": "m3u8-2291-1", "proxy": "p"}  # noqa: SLF001
    RutubeYdlController._prepare_info(source, _info("m3u8-2291-1"))  # noqa: SLF001

    with patch.object(YtDlpController, "_switch_proxy_for_retry") as next_proxy:
        RutubeYdlController._switch_proxy_for_retry(source)  # noqa: SLF001

    next_proxy.assert_called_once_with(source)
    source._create_yt_dlp.assert_called_once_with(  # noqa: SLF001
        {"format": "m3u8-2291-0/default-2291-0/m3u8-2291-1", "proxy": "p"},
    )


def test_retry_before_any_answer_only_changes_the_proxy() -> None:
    """No formats known yet: the format the user picked stays as it is."""
    source = MagicMock()
    source._mirrors = []  # noqa: SLF001

    with patch.object(YtDlpController, "_switch_proxy_for_retry") as next_proxy:
        RutubeYdlController._switch_proxy_for_retry(source)  # noqa: SLF001

    next_proxy.assert_called_once_with(source)
    source._create_yt_dlp.assert_not_called()  # noqa: SLF001


@pytest.mark.anyio
async def test_missing_list_of_qualities_is_asked_again() -> None:
    """Prod 24.11: «no formats» four times, the next request had them."""
    source = MagicMock()
    source.SOURCE = RutubeYdlController.SOURCE
    answers = [DownloadError(NO_FORMATS), DownloadError(NO_FORMATS), {"id": "1"}]

    with patch.object(
        YtDlpController,
        "get_video_info",
        AsyncMock(side_effect=answers),
    ) as read:
        info = await RutubeYdlController.get_video_info(source, URL)

    assert info == {"id": "1"}
    assert read.await_count == INFO_ATTEMPTS
    assert source._switch_proxy_for_retry.call_count == INFO_ATTEMPTS - 1  # noqa: SLF001


@pytest.mark.anyio
async def test_deleted_or_private_video_is_said_so() -> None:
    """Prod 23.11: a private link whose author deleted the video got «Error»."""
    source = MagicMock()
    source.SOURCE = RutubeYdlController.SOURCE
    source._telegram_id = 1  # noqa: SLF001
    source.delete_processing_message = AsyncMock()
    send = AsyncMock()
    source._telegram_bot_controller.send_content_not_found_error = send  # noqa: SLF001

    with patch.object(
        YtDlpController,
        "get_video_info",
        AsyncMock(side_effect=DownloadError(DELETED)),
    ) as read:
        info = await RutubeYdlController.get_video_info(source, URL)

    assert info is None
    assert read.await_count == 1
    send.assert_awaited_once_with(telegram_id=1)


@pytest.mark.anyio
async def test_other_errors_and_the_last_attempt_are_not_hidden() -> None:
    """A different error is raised at once; the third «no formats» too."""
    source = MagicMock()
    source.SOURCE = RutubeYdlController.SOURCE

    with (
        patch.object(
            YtDlpController,
            "get_video_info",
            AsyncMock(side_effect=DownloadError("ERROR: something else")),
        ) as read,
        pytest.raises(DownloadError),
    ):
        await RutubeYdlController.get_video_info(source, URL)
    assert read.await_count == 1

    with (
        patch.object(
            YtDlpController,
            "get_video_info",
            AsyncMock(side_effect=DownloadError(NO_FORMATS)),
        ) as read,
        pytest.raises(DownloadError),
    ):
        await RutubeYdlController.get_video_info(source, URL)
    assert read.await_count == INFO_ATTEMPTS


def test_download_is_retried_twice() -> None:
    """A download that failed gets two more tries, as on ok.ru."""
    assert RutubeYdlController.RETRIES_ON_ERROR == 2
