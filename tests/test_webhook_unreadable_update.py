import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from saver_backend.web.webhook.views import (
    describe_unreadable_update,
    telegram_update_from_webhook,
)


def _request(body: Any = None, error: Exception | None = None) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body, side_effect=error)
    return request


@pytest.mark.anyio
async def test_readable_update_goes_to_the_bot() -> None:
    """A normal update is passed to the dispatcher."""
    controller = MagicMock(feed_update=AsyncMock())
    body = {
        "update_id": 7,
        "message": {
            "message_id": 1,
            "date": 1763900000,
            "chat": {"id": 5, "type": "private"},
            "text": "hi",
        },
    }

    await telegram_update_from_webhook(_request(body), controller)

    update = controller.feed_update.await_args.kwargs["update"]
    assert update.update_id == 7
    assert update.message.text == "hi"


@pytest.mark.anyio
async def test_unreadable_update_is_accepted_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An update aiogram cannot read is not answered with an error."""
    controller = MagicMock(feed_update=AsyncMock())
    body = {
        "update_id": 433827200,
        "message": {"message_id": 1, "date": 1763900000, "text": "secret text"},
    }

    with caplog.at_level(logging.ERROR):
        result = await telegram_update_from_webhook(_request(body), controller)

    assert result is None
    controller.feed_update.assert_not_awaited()
    assert "update_id=433827200" in caplog.text
    assert "message.chat: missing" in caplog.text
    assert "secret text" not in caplog.text


@pytest.mark.anyio
async def test_broken_json_is_accepted(caplog: pytest.LogCaptureFixture) -> None:
    """A body that is not JSON does not make Telegram repeat the request."""
    controller = MagicMock(feed_update=AsyncMock())

    with caplog.at_level(logging.ERROR):
        await telegram_update_from_webhook(
            _request(error=ValueError("not json")),
            controller,
        )

    controller.feed_update.assert_not_awaited()
    assert "ValueError" in caplog.text


def test_description_lists_failed_fields_without_values() -> None:
    """Only field names and error types reach the log."""
    from aiogram.types import Update

    body = {"update_id": 1, "message": {"message_id": "x", "text": "private"}}
    with pytest.raises(ValidationError) as caught:
        Update.model_validate(body)

    text = describe_unreadable_update(body, caught.value)

    assert "keys=['message', 'update_id']" in text
    assert "message.message_id" in text
    assert "private" not in text
