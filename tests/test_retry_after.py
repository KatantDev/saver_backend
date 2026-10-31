from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from saver_backend.services.telegram import retry_after
from saver_backend.services.telegram.retry_after import RetryAfterMiddleware


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
