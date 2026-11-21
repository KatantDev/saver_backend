import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.methods import GetMe, SendMessage, SendVideo
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services import failure_log
from saver_backend.services.telegram.delivery import DeliveryMiddleware
from saver_backend.task_manager import tasks

URL = "https://www.youtube.com/watch?v=mG5inTj8nIE"


def test_error_message_marks_the_request() -> None:
    """The first error shown to the user is the recorded reason."""
    mark = failure_log.start()
    failure_log.mark_failed("video_info_error")
    failure_log.mark_failed("download_error")

    assert failure_log.outcome(mark) == "video_info_error"


def test_delivered_request_is_not_recorded() -> None:
    """A request that delivered a file and showed no error is fine."""
    mark = failure_log.start()
    failure_log.mark_delivered()

    assert failure_log.outcome(mark) is None


def test_silent_request_is_recorded_as_no_result() -> None:
    """Yandex Music on prod hung at 16% and sent nothing: no_result."""
    mark = failure_log.start()

    assert failure_log.outcome(mark) == "no_result"


def test_last_error_from_the_log_is_kept() -> None:
    """The error logged while handling the request is stored with it."""
    mark = failure_log.start()
    try:
        raise ValueError("HTTP Error 403: Forbidden")
    except ValueError:
        logging.getLogger("x").exception("download failed")

    assert mark.error is not None
    assert "download failed" in mark.error
    assert "HTTP Error 403" in mark.error


@pytest.mark.anyio
async def test_parallel_requests_do_not_mix() -> None:
    """Two requests handled at once keep their own marks."""

    async def handle(reason: str | None) -> str | None:
        mark = failure_log.start()
        await asyncio.sleep(0)
        if reason:
            failure_log.mark_failed(reason)
        else:
            failure_log.mark_delivered()
        await asyncio.sleep(0)
        return failure_log.outcome(mark)

    first, second = await asyncio.gather(
        asyncio.create_task(handle("not_found")),
        asyncio.create_task(handle(None)),
    )

    assert first == "not_found"
    assert second is None


async def _through_middleware(method: Any) -> failure_log.FailureMark:
    mark = failure_log.start()

    async def make_request(_bot: Any, _method: Any) -> Any:
        # the chain returns the method's result, not a Response
        return MagicMock()

    await DeliveryMiddleware()(make_request, MagicMock(), method)
    return mark


@pytest.mark.anyio
async def test_sent_video_counts_as_delivered() -> None:
    """A file accepted by Telegram is a delivered result."""
    mark = await _through_middleware(SendVideo(chat_id=1, video="file-id"))

    assert mark.delivered


@pytest.mark.anyio
async def test_get_me_at_startup_passes_through() -> None:
    """Dev 21.11: the app called get_me at startup and crashed on .ok."""
    mark = await _through_middleware(GetMe())

    assert not mark.delivered


@pytest.mark.anyio
async def test_progress_text_is_not_a_result() -> None:
    """«Downloading... 16%» alone does not make the request successful."""
    mark = await _through_middleware(SendMessage(chat_id=1, text="16%"))

    assert not mark.delivered


@pytest.mark.anyio
async def test_quality_menu_counts_as_delivered() -> None:
    """The quality menu without a thumbnail is a message with buttons."""
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="720p", callback_data="q")]],
    )
    mark = await _through_middleware(
        SendMessage(chat_id=1, text="Quality:", reply_markup=markup),
    )

    assert mark.delivered


@pytest.mark.anyio
async def test_failed_request_is_written_with_the_full_link() -> None:
    """The owners asked to keep the full link of every failed download."""
    mark = failure_log.start()
    failure_log.mark_failed("download_error")
    mark.error = "HTTP Error 403: Forbidden"
    db = MagicMock()
    db.session.rollback = AsyncMock()
    db.failed_request_dao.create = AsyncMock()

    await tasks._record_failure(  # noqa: SLF001
        db,
        mark,
        Resolution(source=SourceEnum.YOUTUBE_VIDEO_YDL, url=URL, metadata={}),
        79489201,
        "download",
    )

    db.failed_request_dao.create.assert_awaited_once_with(
        telegram_id=79489201,
        source=SourceEnum.YOUTUBE_VIDEO_YDL,
        host="youtube.com",
        url=URL,
        stage="download",
        reason="download_error",
        error="HTTP Error 403: Forbidden",
    )


@pytest.mark.anyio
async def test_successful_request_is_not_written() -> None:
    """Nothing is stored for a request that went fine."""
    mark = failure_log.start()
    failure_log.mark_delivered()
    db = MagicMock()
    db.failed_request_dao.create = AsyncMock()

    await tasks._record_failure(  # noqa: SLF001
        db,
        mark,
        Resolution(source=SourceEnum.YOUTUBE_VIDEO_YDL, url=URL, metadata={}),
        1,
        "download",
    )

    db.failed_request_dao.create.assert_not_awaited()
