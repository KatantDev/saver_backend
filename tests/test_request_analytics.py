import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.telegram.request_report_service import (
    RequestReportService,
)
from saver_backend.telegram_bot.middlewares.request_log import RequestLogMiddleware


def _message(text: str) -> Message:
    return Message.model_construct(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat.model_construct(id=1, type="private"),
        text=text,
    )


async def _run(text: str) -> AsyncMock:
    """Run the middleware on a text message and return the request-log DAO mock."""
    dao = AsyncMock()
    user = MagicMock(id=uuid.uuid4())
    handler = AsyncMock(return_value="ok")
    data = {"user": user, "request_log_dao": dao}
    result = await RequestLogMiddleware()(handler, _message(text), data)
    assert result == "ok"
    handler.assert_awaited_once()
    return dao


@pytest.mark.anyio
async def test_logs_supported_request() -> None:
    """A supported link is logged with its host and resolved source."""
    dao = await _run("https://www.instagram.com/reel/De4BW9IyrFr/")

    dao.create.assert_awaited_once()
    kwargs = dao.create.await_args.kwargs
    assert kwargs["host"] == "instagram.com"
    assert kwargs["source"] == SourceEnum.INSTAGRAM_INDOWN


@pytest.mark.anyio
async def test_logs_unsupported_host() -> None:
    """An unsupported link is still logged, as UNSUPPORTED, by host."""
    dao = await _run("look at https://example.com/some/video")

    dao.create.assert_awaited_once()
    kwargs = dao.create.await_args.kwargs
    assert kwargs["host"] == "example.com"
    assert kwargs["source"] == SourceEnum.UNSUPPORTED


@pytest.mark.anyio
async def test_ignores_messages_without_a_link() -> None:
    """Plain chat with no URL is not logged."""
    dao = await _run("hello there, how are you")

    dao.create.assert_not_awaited()


@pytest.mark.anyio
async def test_skips_when_user_missing() -> None:
    """Without a user (e.g. a bot message) nothing is logged and flow continues."""
    dao = AsyncMock()
    handler = AsyncMock(return_value="ok")
    data = {"request_log_dao": dao}
    result = await RequestLogMiddleware()(
        handler,
        _message("https://www.tiktok.com/@x/video/1"),
        data,
    )
    assert result == "ok"
    dao.create.assert_not_awaited()


@pytest.mark.anyio
async def test_report_groups_supported_and_unsupported() -> None:
    """The report separates platforms from unsupported hosts."""
    dao = AsyncMock()
    dao.get_count.return_value = 10
    dao.get_counts_by_source.return_value = [
        (SourceEnum.TIKTOK, 5),
        (SourceEnum.UNSUPPORTED, 3),
        (SourceEnum.INSTAGRAM_INDOWN, 2),
    ]
    dao.get_top_unsupported_hosts.return_value = [
        ("spotify.com", 2),
        ("threads.net", 1),
    ]

    report = await RequestReportService(dao).construct(days=7)

    assert "Запросы за 7 дн." in report
    assert "Всего запросов: 10" in report
    assert "Поддерживаемые: 7" in report
    assert "Не поддерживаются: 3" in report
    assert "Tiktok: 5" in report
    assert "Instagram Indown: 2" in report
    assert "spotify.com: 2" in report
    # the unsupported pseudo-source is not shown as a platform line
    assert "Unsupported:" not in report
