from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders import instagram_ydl_source
from saver_backend.services.downloaders.instagram_ydl_source import (
    InstagramYdlController,
)


def _controller() -> InstagramYdlController:
    return InstagramYdlController(
        resolution=Resolution(
            source=SourceEnum.INSTAGRAM_YDL,
            url="https://www.instagram.com/reel/DejbPYGM119/",
            metadata={"code": "DejbPYGM119", "type": "reels"},
        ),
        telegram_bot_controller=AsyncMock(),
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=AsyncMock(),
    )


@pytest.mark.anyio
async def test_uses_service_account_cookies(tmp_path: Path) -> None:
    """The session cookies are used whenever they exist."""
    session = tmp_path / "cookies_session.txt"
    session.write_text("# Netscape HTTP Cookie File\n")
    with patch.object(instagram_ydl_source, "YDL_COOKIES_FILE", session):
        controller = _controller()
    assert controller._base_options["cookiefile"] == str(session)  # noqa: SLF001


@pytest.mark.anyio
async def test_without_session_falls_back_to_cookie_folder(tmp_path: Path) -> None:
    """Without a session the usual random pick from the folder is kept."""
    with (
        patch.object(
            instagram_ydl_source, "YDL_COOKIES_FILE", tmp_path / "missing.txt"
        ),
        patch.object(
            instagram_ydl_source.YtDlpController,
            "_set_cookies",
            autospec=True,
        ) as base_set_cookies,
    ):
        _controller()
    base_set_cookies.assert_called_once()
