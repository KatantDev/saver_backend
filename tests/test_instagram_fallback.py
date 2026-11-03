from unittest.mock import AsyncMock, patch

import pytest

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders import instagram_indown_source
from saver_backend.services.downloaders.instagram_indown_source import (
    InstagramInDownController,
)

URL = "https://www.instagram.com/reel/DejbPYGM119/"


def _controller() -> tuple[InstagramInDownController, AsyncMock]:
    cache_dao = AsyncMock()
    cache_dao.get_by_filters.return_value = None
    telegram = AsyncMock()
    telegram.get_fsm_data.return_value = None
    controller = InstagramInDownController(
        resolution=Resolution(
            source=SourceEnum.INSTAGRAM_INDOWN,
            url=URL,
            metadata={"code": "DejbPYGM119", "type": "reels"},
        ),
        telegram_bot_controller=telegram,
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=cache_dao,
        message_id=1,
    )
    return controller, telegram


@pytest.mark.anyio
async def test_falls_back_to_ydl_when_indown_finds_nothing() -> None:
    """yt-dlp is used when indown.io returns no media."""
    controller, telegram = _controller()
    form_fields = ["token", "https://indown.io/", "e", "e"]
    response = AsyncMock()
    response.text = "<html></html>"
    response.raise_for_status = lambda: None

    ydl = AsyncMock()
    with (
        patch.object(
            controller,
            "_get_form_fields",
            AsyncMock(return_value=form_fields),
        ),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            return_value=ydl,
        ) as ydl_cls,
        patch("httpx.AsyncClient.post", AsyncMock(return_value=response)),
    ):
        await controller.download_video()

    ydl_cls.assert_called_once()
    ydl.download_video.assert_awaited_once()
    ydl.close.assert_awaited_once()
    telegram.send_error_downloading.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_sends_error_when_fallback_fails() -> None:
    """User gets an error message when yt-dlp fails too."""
    controller, telegram = _controller()
    ydl = AsyncMock()
    ydl.message_id = 1
    ydl.download_video.side_effect = RuntimeError("empty media response")

    with (
        patch.object(controller, "_get_form_fields", AsyncMock(return_value=None)),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            return_value=ydl,
        ),
    ):
        await controller.download_video()

    ydl.close.assert_awaited_once()
    telegram.send_error_downloading.assert_awaited_once()
    await controller.close()
