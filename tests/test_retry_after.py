import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage, SendVideo
from aiogram.types import FSInputFile

from saver_backend.services.telegram import retry_after
from saver_backend.services.telegram.retry_after import (
    UPLOAD_ATTEMPTS,
    RetryAfterMiddleware,
)


def _flood(seconds: int) -> TelegramRetryAfter:
    return TelegramRetryAfter(
        method=MagicMock(),
        message="Too Many Requests",
        retry_after=seconds,
    )


@pytest.mark.anyio
async def test_repeats_request_after_flood_pause() -> None:
    """The request goes through after Telegram asked to wait twice."""
    response = MagicMock()
    make_request = AsyncMock(side_effect=[_flood(1), _flood(2), response])
    method = SendMessage(chat_id=1, text="hi")
    sleep = AsyncMock()

    with patch.object(retry_after.asyncio, "sleep", sleep):
        result = await RetryAfterMiddleware()(make_request, MagicMock(), method)

    assert result is response
    assert make_request.await_count == 3
    assert [c.args[0] for c in sleep.await_args_list] == [1, 2]
    assert all(c.args[1] is method for c in make_request.await_args_list)


@pytest.mark.anyio
async def test_gives_up_after_max_attempts() -> None:
    """After the last attempt the flood error is passed on."""
    make_request = AsyncMock(side_effect=[_flood(1), _flood(1), _flood(1)])

    with (
        patch.object(retry_after.asyncio, "sleep", AsyncMock()),
        pytest.raises(TelegramRetryAfter),
    ):
        await RetryAfterMiddleware(max_attempts=3)(
            make_request,
            MagicMock(),
            SendMessage(chat_id=1, text="hi"),
        )

    assert make_request.await_count == 3


@pytest.mark.anyio
async def test_does_not_wait_for_long_pause() -> None:
    """A pause longer than max_wait is not waited for."""
    make_request = AsyncMock(side_effect=[_flood(300)])
    sleep = AsyncMock()

    with (
        patch.object(retry_after.asyncio, "sleep", sleep),
        pytest.raises(TelegramRetryAfter),
    ):
        await RetryAfterMiddleware(max_wait=30)(
            make_request,
            MagicMock(),
            SendMessage(chat_id=1, text="hi"),
        )

    sleep.assert_not_awaited()


def _flood_as_bad_request(seconds: int) -> "TelegramBadRequest":
    return TelegramBadRequest(
        method=MagicMock(),
        message=f"Bad Request: too Many Requests: retry after {seconds}",
    )


@pytest.mark.anyio
async def test_repeats_after_flood_pause_reported_as_bad_request() -> None:
    """The local Bot API server reports the pause of an upload as a 400."""
    response = MagicMock()
    make_request = AsyncMock(side_effect=[_flood_as_bad_request(8), response])
    sleep = AsyncMock()

    with patch.object(retry_after.asyncio, "sleep", sleep):
        result = await RetryAfterMiddleware()(
            make_request,
            MagicMock(),
            SendMessage(chat_id=1, text="hi"),
        )

    assert result is response
    assert [c.args[0] for c in sleep.await_args_list] == [8]


@pytest.mark.anyio
async def test_other_bad_request_is_not_repeated() -> None:
    """A real refusal is passed on at once."""
    error = TelegramBadRequest(method=MagicMock(), message="Bad Request: not Found")
    make_request = AsyncMock(side_effect=[error])
    sleep = AsyncMock()

    with (
        patch.object(retry_after.asyncio, "sleep", sleep),
        pytest.raises(TelegramBadRequest),
    ):
        await RetryAfterMiddleware()(
            make_request,
            MagicMock(),
            SendMessage(chat_id=1, text="hi"),
        )

    assert make_request.await_count == 1
    sleep.assert_not_awaited()


def _upload(tmp_path: Path, chat_id: int = 1) -> SendVideo:
    file = tmp_path / f"video{chat_id}.mp4"
    file.write_bytes(b"video")
    return SendVideo(chat_id=chat_id, video=FSInputFile(path=file))


@pytest.mark.anyio
async def test_upload_is_repeated_more_times(tmp_path: Path) -> None:
    """Prod 25-26.11: a finished download was lost after three refusals."""
    response = MagicMock()
    refusals = [_flood_as_bad_request(8)] * (UPLOAD_ATTEMPTS - 1)
    make_request = AsyncMock(side_effect=[*refusals, response])
    sleep = AsyncMock()

    with patch.object(retry_after.asyncio, "sleep", sleep):
        result = await RetryAfterMiddleware()(
            make_request, MagicMock(), _upload(tmp_path)
        )

    assert result is response
    assert make_request.await_count == UPLOAD_ATTEMPTS


@pytest.mark.anyio
async def test_other_requests_to_a_paused_chat_wait_first() -> None:
    """A request to the chat Telegram just paused waits instead of being refused."""
    middleware = RetryAfterMiddleware()
    response = MagicMock()
    sleep = AsyncMock()

    with patch.object(retry_after.asyncio, "sleep", sleep):
        await middleware(
            AsyncMock(side_effect=[_flood(8), response]),
            MagicMock(),
            SendMessage(chat_id=1, text="first"),
        )
        assert 0 < middleware.pause_left(1) <= 8
        assert middleware.pause_left(2) == 0
        sleep.reset_mock()
        await middleware(
            AsyncMock(return_value=response),
            MagicMock(),
            SendMessage(chat_id=1, text="second"),
        )
        waited = [c.args[0] for c in sleep.await_args_list]
        sleep.reset_mock()
        await middleware(
            AsyncMock(return_value=response),
            MagicMock(),
            SendMessage(chat_id=2, text="other chat"),
        )

    assert len(waited) == 1
    assert 0 < waited[0] <= 8
    sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_pause_of_a_chat_ends() -> None:
    """When the pause is over the chat is forgotten."""
    middleware = RetryAfterMiddleware()
    middleware._paused_until[1] = time.monotonic() - 1  # noqa: SLF001

    assert middleware.pause_left(1) == 0
    assert middleware._paused_until == {}  # noqa: SLF001
    assert middleware.pause_left(None) == 0
    assert middleware.pause_left("@channel") == 0


@pytest.mark.anyio
async def test_uploads_to_one_chat_go_one_at_a_time(tmp_path: Path) -> None:
    """Prod 25.11: nine uploads at once into one chat, all refused together."""
    middleware = RetryAfterMiddleware()
    running = 0
    most = 0
    other_chat_started = asyncio.Event()

    async def make_request(_bot: Any, method: Any) -> MagicMock:
        nonlocal running, most
        if method.chat_id == 2:
            other_chat_started.set()
            return MagicMock()
        running += 1
        most = max(most, running)
        await other_chat_started.wait()
        await asyncio.sleep(0)
        running -= 1
        return MagicMock()

    await asyncio.gather(
        middleware(make_request, MagicMock(), _upload(tmp_path)),
        middleware(make_request, MagicMock(), _upload(tmp_path)),
        middleware(make_request, MagicMock(), _upload(tmp_path)),
        # another chat is not held back by the queue of the first one
        middleware(make_request, MagicMock(), _upload(tmp_path, chat_id=2)),
    )

    assert most == 1
    assert middleware._upload_locks == {}  # noqa: SLF001


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        TelegramNetworkError(
            method=MagicMock(),
            message="ServerDisconnectedError: Server disconnected",
        ),
        TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: internal Server Error during file upload",
        ),
    ],
)
async def test_dropped_upload_is_sent_once_more(
    tmp_path: Path,
    error: Exception,
) -> None:
    """Prod 24-26.11: the connection dropped in the middle of an upload."""
    response = MagicMock()
    make_request = AsyncMock(side_effect=[error, response])

    result = await RetryAfterMiddleware()(make_request, MagicMock(), _upload(tmp_path))

    assert result is response

    make_request = AsyncMock(side_effect=[error, error])
    with pytest.raises(type(error)):
        await RetryAfterMiddleware()(make_request, MagicMock(), _upload(tmp_path))
    assert make_request.await_count == 2


@pytest.mark.anyio
async def test_timeout_and_plain_requests_are_not_sent_again(tmp_path: Path) -> None:
    """After a timeout the file may still arrive; a text has nothing to resend."""
    timeout = TelegramNetworkError(method=MagicMock(), message="Request timeout error")
    make_request = AsyncMock(side_effect=[timeout])
    with pytest.raises(TelegramNetworkError):
        await RetryAfterMiddleware()(make_request, MagicMock(), _upload(tmp_path))
    assert make_request.await_count == 1

    dropped = TelegramNetworkError(method=MagicMock(), message="Server disconnected")
    make_request = AsyncMock(side_effect=[dropped])
    with pytest.raises(TelegramNetworkError):
        await RetryAfterMiddleware()(
            make_request,
            MagicMock(),
            SendMessage(chat_id=1, text="hi"),
        )
    assert make_request.await_count == 1
