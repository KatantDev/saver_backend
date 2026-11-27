import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramNetworkError

from saver_backend.services.telegram.startup_check import get_bot_user


@pytest.mark.anyio
async def test_returns_bot_user() -> None:
    """A server that answers gives the bot user and logs nothing."""
    user = MagicMock()
    bot = MagicMock(get_me=AsyncMock(return_value=user))

    assert await get_bot_user(bot, "worker") is user


@pytest.mark.anyio
async def test_explains_silent_server(caplog: pytest.LogCaptureFixture) -> None:
    """A server that does not answer is named in the log before the exit."""
    error = TelegramNetworkError(method=MagicMock(), message="Request timeout error")
    bot = MagicMock(get_me=AsyncMock(side_effect=error))

    with caplog.at_level(logging.ERROR), pytest.raises(TelegramNetworkError):
        await get_bot_user(bot, "worker")

    assert "worker cannot start: the Bot API server" in caplog.text
    assert "did not answer getMe" in caplog.text
    assert "Request timeout error" in caplog.text
