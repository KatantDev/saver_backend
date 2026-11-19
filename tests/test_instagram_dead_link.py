from unittest.mock import AsyncMock, patch

import pytest
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders.instagram_ydl_source import (
    InstagramYdlController,
    is_content_unavailable,
)
from saver_backend.services.downloaders.ydl_source import YtDlpController

EMPTY_RESPONSE = (
    "ERROR: [Instagram] CxQ9zzzzzz1: Instagram sent an empty media response. "
    "Check if this post is accessible in your browser without being logged-in."
)


@pytest.mark.parametrize(
    ("message", "unavailable"),
    [
        (EMPTY_RESPONSE, True),
        ("ERROR: [Instagram] x: Requested content is not available", True),
        ("ERROR: [Instagram] x: HTTP Error 404: Not Found", True),
        ("ERROR: Unable to download webpage: Connection reset by peer", False),
        ("ERROR: [Instagram] x: HTTP Error 429: Too Many Requests", False),
    ],
)
def test_unavailable_post_is_told_apart_from_failures(
    message: str,
    unavailable: bool,
) -> None:
    """Deleted or private posts differ from network and rate-limit errors."""
    assert is_content_unavailable(DownloadError(message)) is unavailable


def _controller(inline: bool = False) -> InstagramYdlController:
    controller = InstagramYdlController(
        resolution=Resolution(
            source=SourceEnum.INSTAGRAM_YDL,
            url="https://www.instagram.com/reel/CxQ9zzzzzz1/",
            metadata={"code": "CxQ9zzzzzz1", "type": "reels"},
        ),
        telegram_bot_controller=AsyncMock(),
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=AsyncMock(),
        inline_query_id="q" if inline else None,
    )
    controller.delete_processing_message = AsyncMock()  # type: ignore[method-assign]
    return controller


@pytest.mark.anyio
async def test_dead_reel_gets_private_or_deleted_answer() -> None:
    """IG-05: a dead reel answers «private or deleted», not a generic error (#18)."""
    controller = _controller()

    with patch.object(
        YtDlpController,
        "get_video_info",
        AsyncMock(side_effect=DownloadError(EMPTY_RESPONSE)),
    ):
        info = await controller.get_video_info(
            "https://www.instagram.com/reel/CxQ9zzzzzz1/",
        )

    assert info is None
    controller.delete_processing_message.assert_awaited_once()
    controller._telegram_bot_controller.send_content_not_found_error.assert_awaited_once_with(  # noqa: SLF001
        telegram_id=1,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("message", "inline"),
    [
        ("ERROR: Unable to download webpage: Connection reset by peer", False),
        (EMPTY_RESPONSE, True),
    ],
)
async def test_other_errors_go_the_old_way(message: str, inline: bool) -> None:
    """Network errors and inline queries keep the old error handling."""
    controller = _controller(inline=inline)

    with (
        patch.object(
            YtDlpController,
            "get_video_info",
            AsyncMock(side_effect=DownloadError(message)),
        ),
        pytest.raises(DownloadError),
    ):
        await controller.get_video_info("https://x")

    controller._telegram_bot_controller.send_content_not_found_error.assert_not_awaited()  # noqa: SLF001
