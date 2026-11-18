from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from saver_backend.entities.enums import ContentTypeEnum
from saver_backend.telegram_bot.handlers import download
from saver_backend.telegram_bot.keyboards.callback import VideoFormatCallback


def _query(message_id: int) -> MagicMock:
    query = MagicMock()
    query.message = MagicMock(spec=Message)
    query.message.message_id = message_id
    query.message.edit_reply_markup = AsyncMock()
    query.answer = AsyncMock()
    return query


@pytest.mark.anyio
async def test_button_under_old_menu_is_refused() -> None:
    """A button under an older menu no longer downloads the latest link (#22)."""
    query = _query(message_id=10)

    with patch.object(download, "_", lambda text, **_kw: text):
        refused = await download._reject_stale_menu(  # noqa: SLF001
            query,
            {"quality_selection_message_id": 20},
        )

    assert refused is True
    query.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    query.answer.assert_awaited_once_with("format selection expired", show_alert=True)


@pytest.mark.anyio
@pytest.mark.parametrize("fsm_data", [{"quality_selection_message_id": 10}, {}])
async def test_current_menu_or_empty_state_passes(fsm_data: dict[str, int]) -> None:
    """The current menu works; an empty state is left to the expiry message."""
    query = _query(message_id=10)

    refused = await download._reject_stale_menu(query, fsm_data)  # noqa: SLF001

    assert refused is False
    query.message.edit_reply_markup.assert_not_awaited()
    query.answer.assert_not_awaited()


@pytest.mark.anyio
async def test_old_menu_does_not_start_a_download() -> None:
    """Pressing «1080p» under an old menu starts no download at all."""
    query = _query(message_id=10)
    query.from_user = MagicMock(id=1)
    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "quality_selection_message_id": 20,
            "video_dto": {"url": "https://youtu.be/latest", "source_id": "latest"},
            "resolution": {"url": "https://youtu.be/latest"},
        },
    )
    callback = VideoFormatCallback(label="1080p", contenttype=ContentTypeEnum.VIDEO)

    with (
        patch.object(download, "_", lambda text, **_kw: text),
        patch.object(download, "_trigger_download", AsyncMock()) as trigger,
    ):
        await download.on_format_select(query, callback, state)

    trigger.assert_not_awaited()
