from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest

from saver_backend.services import failure_log
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.telegram import bot_controller
from saver_backend.services.telegram.bot_controller import (
    TelegramBotController,
    _cover_is_to_blame,
)
from saver_backend.services.telegram.retry_after import RetryAfterMiddleware


def _controller() -> TelegramBotController:
    controller = TelegramBotController.__new__(TelegramBotController)
    controller._bot = MagicMock()  # noqa: SLF001
    controller._bot.answer_inline_query = AsyncMock()  # noqa: SLF001
    controller._bot.edit_message_text = AsyncMock()  # noqa: SLF001
    controller._bot.default.parse_mode = "HTML"  # noqa: SLF001
    controller._closed_inline_queries = {}  # noqa: SLF001
    controller._flood_control = RetryAfterMiddleware()  # noqa: SLF001
    return controller


def _video() -> VideoDTO:
    return VideoDTO(source_id="1", url="https://example.com/v", title="clip")


@pytest.mark.anyio
async def test_slow_inline_query_is_closed_with_a_hint() -> None:
    """A late download answers "still preparing" and later answers are skipped."""
    controller = _controller()
    mark = failure_log.start()

    with patch.object(bot_controller, "_", lambda text, **_kw: text):
        await controller.answer_inline_query_preparing("q1")
        await controller.answer_inline_query_cached_video("q1", _video(), "file")
        await controller.answer_inline_query_video("q1", _video())
        await controller.answer_inline_query_error("q1")

    answer = controller._bot.answer_inline_query  # noqa: SLF001
    assert answer.call_count == 1
    article = answer.call_args.kwargs["results"][0]
    assert article.title == "inline query preparing title"
    assert mark.reason == "inline_slow"


@pytest.mark.anyio
async def test_other_inline_queries_are_answered_as_before() -> None:
    """Closing one inline query does not silence another."""
    controller = _controller()

    with patch.object(bot_controller, "_", lambda text, **_kw: text):
        await controller.answer_inline_query_preparing("q1")
        await controller.answer_inline_query_cached_video("q2", _video(), "file")

    assert controller._bot.answer_inline_query.call_count == 2  # noqa: SLF001


@pytest.mark.anyio
async def test_user_without_chat_gets_the_start_button() -> None:
    """A user who never started the bot is offered to start it."""
    controller = _controller()
    mark = failure_log.start()

    with patch.object(bot_controller, "_", lambda text, **_kw: text):
        await controller.answer_inline_query_error(
            "q1",
            error_text="blocked",
            start_button=True,
        )

    button = controller._bot.answer_inline_query.call_args.kwargs["button"]  # noqa: SLF001
    assert button.text == "inline start bot button"
    assert button.start_parameter == "inline"
    assert mark.reason == "bot_not_started"


@pytest.mark.anyio
async def test_plain_inline_error_has_no_button() -> None:
    """An ordinary error keeps the old answer."""
    controller = _controller()
    mark = failure_log.start()

    with patch.object(bot_controller, "_", lambda text, **_kw: text):
        await controller.answer_inline_query_error("q1")

    assert controller._bot.answer_inline_query.call_args.kwargs["button"] is None  # noqa: SLF001
    assert mark.reason == "inline_error"


@pytest.mark.anyio
async def test_failed_progress_edit_is_not_an_error() -> None:
    """A progress line that cannot be edited leaves no error in the journal."""
    controller = _controller()
    controller.language = "en"
    controller._bot.edit_message_text = AsyncMock(  # noqa: SLF001
        side_effect=TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: MESSAGE_ID_INVALID",
        ),
    )
    mark = failure_log.start()

    with patch.object(bot_controller, "_", lambda text, **_kw: "{percent}"):
        await controller.send_update_downloading(1, 2, 50)

    assert mark.error is None
    assert mark.reason is None


def test_cover_is_blamed_only_when_it_can_be_the_reason() -> None:
    """A cover file is dropped only on Telegram's picture error."""
    picture_error = TelegramBadRequest(
        method=MagicMock(),
        message="Bad Request: IMAGE_PROCESS_FAILED",
    )
    other_error = TelegramBadRequest(
        method=MagicMock(),
        message="Bad Request: FILE_PARTS_INVALID",
    )
    cover_file = MagicMock()

    assert _cover_is_to_blame("https://example.com/c.jpg", other_error) is True
    assert _cover_is_to_blame(cover_file, picture_error) is True
    assert _cover_is_to_blame(cover_file, other_error) is False
    assert _cover_is_to_blame(None, picture_error) is False
