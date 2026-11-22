from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders import ydl_source
from saver_backend.services.downloaders.exceptions import IPAddressBlockedError
from saver_backend.services.downloaders.ydl_source import YtDlpController
from saver_backend.services.downloaders.youtube_shorts_ydl_source import (
    YouTubeShortsYdlController,
)
from saver_backend.services.downloaders.youtube_video_ydl_source import (
    YouTubeVideoYdlController,
)


def _source(retries: int, fetch_results: list[object]) -> MagicMock:
    source = MagicMock()
    source.SOURCE = SourceEnum.YOUTUBE_VIDEO_YDL
    source.RETRIES_ON_ERROR = retries
    source.DIRECT_FALLBACK = False
    source._selected_format_id = None  # noqa: SLF001
    source._resolution.metadata = {}  # noqa: SLF001
    source._resolution.url = "https://youtu.be/x"  # noqa: SLF001
    source.send_video_from_cache = AsyncMock(return_value=False)
    source._fetch_and_send = AsyncMock(side_effect=fetch_results)  # noqa: SLF001
    return source


def test_youtube_sources_retry_once() -> None:
    """Video and shorts get one retry, other yt-dlp sources keep failing fast."""
    assert YouTubeVideoYdlController.RETRIES_ON_ERROR == 1
    assert YouTubeShortsYdlController.RETRIES_ON_ERROR == 1
    assert YtDlpController.RETRIES_ON_ERROR == 0


@pytest.mark.anyio
async def test_failed_attempt_is_retried_through_another_proxy() -> None:
    """A transient yt-dlp error is retried once, the user gets the video."""
    source = _source(retries=1, fetch_results=[DownloadError("Connection reset"), None])

    with patch.object(ydl_source, "RETRY_PAUSE_SECONDS", 0):
        await YtDlpController.download_video(source)

    assert source._fetch_and_send.await_count == 2  # noqa: SLF001
    first, second = source._fetch_and_send.await_args_list  # noqa: SLF001
    assert first.kwargs["raise_errors"] is True
    assert second.kwargs["raise_errors"] is False
    source._switch_proxy_for_retry.assert_called_once_with()  # noqa: SLF001


@pytest.mark.anyio
async def test_second_failure_is_raised_as_before() -> None:
    """If the retry fails too, the error goes up exactly as without the retry."""
    source = _source(
        retries=1,
        fetch_results=[DownloadError("first"), DownloadError("second")],
    )

    with (
        patch.object(ydl_source, "RETRY_PAUSE_SECONDS", 0),
        pytest.raises(DownloadError, match="second"),
    ):
        await YtDlpController.download_video(source)


@pytest.mark.anyio
async def test_sources_without_retry_try_once() -> None:
    """With no retries the only attempt reports errors to the user itself."""
    source = _source(retries=0, fetch_results=[None])

    await YtDlpController.download_video(source)

    source._fetch_and_send.assert_awaited_once()  # noqa: SLF001
    assert source._fetch_and_send.await_args.kwargs["raise_errors"] is False  # noqa: SLF001
    source._switch_proxy_for_retry.assert_not_called()  # noqa: SLF001


@pytest.mark.anyio
@pytest.mark.parametrize("raise_errors", [True, False])
async def test_info_error_is_raised_only_before_the_last_attempt(
    raise_errors: bool,
) -> None:
    """Before the last attempt the error is raised for a retry, then reported."""
    source = MagicMock()
    source.get_video_info = AsyncMock(side_effect=DownloadError("unavailable"))
    source._send_error_message = AsyncMock()  # noqa: SLF001

    if raise_errors:
        with pytest.raises(DownloadError):
            await YtDlpController._fetch_and_send(source, "best", raise_errors=True)  # noqa: SLF001
        source._send_error_message.assert_not_awaited()  # noqa: SLF001
    else:
        await YtDlpController._fetch_and_send(source, "best", raise_errors=False)  # noqa: SLF001
        source._send_error_message.assert_awaited_once()  # noqa: SLF001


def test_retry_without_spare_proxies_starts_a_fresh_session() -> None:
    """When no proxy is left, the retry still gets a new yt-dlp session."""
    source = MagicMock()
    source._set_proxy.side_effect = IPAddressBlockedError()  # noqa: SLF001
    fresh = MagicMock()
    source._create_yt_dlp.return_value = fresh  # noqa: SLF001

    YtDlpController._switch_proxy_for_retry(source)  # noqa: SLF001

    assert source._yt_dlp is fresh  # noqa: SLF001
