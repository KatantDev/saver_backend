from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from saver_backend.services.downloaders.schema import AudioDTO, is_live_stream
from saver_backend.services.downloaders.yandex_ymdantic import (
    YmdanticController,
    is_lossless_codec,
)
from saver_backend.task_manager import tasks


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"is_live": True}, True),
        ({"live_status": "is_live"}, True),
        ({"live_status": "is_upcoming"}, True),
        ({"live_status": "post_live"}, True),
        ({"live_status": "was_live", "is_live": False}, False),
        ({"live_status": "not_live"}, False),
        ({}, False),
    ],
)
def test_live_stream_detection(info: dict[str, Any], *, expected: bool) -> None:
    """Running, scheduled and still processing streams are refused."""
    assert is_live_stream(info) is expected


async def _run_get_video_info(info: dict[str, Any]) -> MagicMock:
    bot = MagicMock()
    bot.edit_video_is_live = AsyncMock()
    bot.edit_video_no_formats = AsyncMock()
    bot.edit_failed_video_info = AsyncMock()
    bot.send_choose_quality = AsyncMock(return_value=None)
    bot.delete_message = AsyncMock()

    controller = MagicMock()
    controller.set_user_language = AsyncMock()
    controller.get_video_info = AsyncMock(return_value=info)
    controller.close = AsyncMock()
    controller.message_id = 7

    state = MagicMock()
    state.telegram_bot_controller = bot
    state.source_resolver.get_controller.return_value = MagicMock(
        return_value=controller
    )
    db = MagicMock()

    func = getattr(tasks.get_video_info, "original_func", tasks.get_video_info)
    await func(
        resolution=MagicMock(url="https://www.youtube.com/live/ROboUHfPUrU"),
        telegram_id=1,
        processing_message_id=7,
        state=state,
        db=db,
    )
    return bot


@pytest.mark.anyio
async def test_live_stream_gets_its_own_message() -> None:
    """YT-04: a running broadcast is explained, not «No formats available»."""
    bot = await _run_get_video_info(
        {"id": "ROboUHfPUrU", "is_live": True, "live_status": "is_live"},
    )

    bot.edit_video_is_live.assert_awaited_once_with(telegram_id=1, message_id=7)
    bot.edit_video_no_formats.assert_not_awaited()


@pytest.mark.anyio
async def test_finished_broadcast_is_not_refused() -> None:
    """A recording of an ended stream goes the usual way."""
    bot = await _run_get_video_info({"id": "x", "live_status": "was_live"})

    bot.edit_video_is_live.assert_not_awaited()


@pytest.mark.parametrize(
    ("codec", "expected"),
    [
        ("flac-mp4", True),
        ("FLAC-mp4", True),
        ("aac-mp4", False),
        ("he-aac-mp4", False),
        ("mp3", False),
        ("flac", False),
        ("", False),
        (None, False),
    ],
)
def test_lossless_codec(codec: str | None, *, expected: bool) -> None:
    """Only a FLAC file counts as lossless."""
    assert is_lossless_codec(codec) is expected


def _source_with_codec(codec: str) -> MagicMock:
    source = MagicMock()
    source.CODECSEPARATOR = YmdanticController.CODECSEPARATOR
    source._client.get_track_file_info = AsyncMock(  # noqa: SLF001
        return_value=MagicMock(codec=codec, url="https://example.com/file"),
    )
    source._client.get_track = AsyncMock(return_value=MagicMock())  # noqa: SLF001
    return source


@pytest.mark.anyio
async def test_aac_track_gets_no_lossless_button() -> None:
    """YM-02: track 38831880 answered aac-mp4, smaller than the MP3."""
    source = _source_with_codec("aac-mp4")

    dto = await YmdanticController._get_track_in_flac(source, 38831880)  # noqa: SLF001

    assert dto is None


@pytest.mark.anyio
async def test_flac_track_keeps_lossless_button() -> None:
    """A real FLAC still gets the button."""
    source = _source_with_codec("flac-mp4")
    expected = AudioDTO(source_id="1", quality="flac", flac=True)

    with patch.object(AudioDTO, "from_yandmatic", return_value=expected):
        dto = await YmdanticController._get_track_in_flac(source, 1)  # noqa: SLF001

    assert dto is expected


@pytest.mark.anyio
async def test_album_lossless_check_skips_aac() -> None:
    """The album button is offered only when some track is FLAC."""
    source = _source_with_codec("aac-mp4")
    dtos = [AudioDTO(source_id="1"), AudioDTO(source_id="2")]

    hq = await YmdanticController._get_hq_dtos(source, dtos, check=True)  # noqa: SLF001

    assert hq == []
