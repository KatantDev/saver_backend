from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import VideoDTO


def _source(send_result: object) -> MagicMock:
    source = MagicMock()
    source.SOURCE = SourceEnum.YOUTUBE_SHORTS_YDL
    source._inline_query_id = None  # noqa: SLF001
    source._telegram_id = 1  # noqa: SLF001
    cached = MagicMock()
    cached.file_id = "broken-file-id"
    cached.meta_data_dto = VideoDTO(url="https://youtu.be/x", source_id="x")
    source._cache_dao.get_by_filters = AsyncMock(return_value=cached)  # noqa: SLF001
    source._cache_dao.delete_by_file_id = AsyncMock()  # noqa: SLF001
    send = AsyncMock()
    if isinstance(send_result, Exception):
        send.side_effect = send_result
    else:
        send.return_value = send_result
    source._telegram_bot_controller.send_video_by_file_id = send  # noqa: SLF001
    source._create_history_entry = AsyncMock()  # noqa: SLF001
    source.delete_processing_message = AsyncMock()
    return source


@pytest.mark.anyio
async def test_rejected_file_id_is_dropped_and_downloaded_again() -> None:
    """A cached file Telegram refuses is removed and the video is downloaded."""
    source = _source(
        TelegramBadRequest(method=MagicMock(), message="wrong file identifier"),
    )

    sent = await BaseSourceController.send_video_from_cache(source, "x", "best")

    assert sent is False
    source._cache_dao.delete_by_file_id.assert_awaited_once_with(  # noqa: SLF001
        source=SourceEnum.YOUTUBE_SHORTS_YDL,
        file_id="broken-file-id",
    )
    source._create_history_entry.assert_not_awaited()  # noqa: SLF001
    source.delete_processing_message.assert_not_awaited()


@pytest.mark.anyio
async def test_cached_video_is_sent_and_recorded() -> None:
    """A working cached file is sent, recorded in history and kept."""
    source = _source(MagicMock())

    sent = await BaseSourceController.send_video_from_cache(source, "x", "best")

    assert sent is True
    source._create_history_entry.assert_awaited_once()  # noqa: SLF001
    source.delete_processing_message.assert_awaited_once()
    source._cache_dao.delete_by_file_id.assert_not_awaited()  # noqa: SLF001


@pytest.mark.anyio
async def test_blocked_bot_does_not_drop_cache() -> None:
    """When the user blocked the bot the cache entry stays."""
    source = _source(None)

    sent = await BaseSourceController.send_video_from_cache(source, "x", "best")

    assert sent is True
    source._cache_dao.delete_by_file_id.assert_not_awaited()  # noqa: SLF001
