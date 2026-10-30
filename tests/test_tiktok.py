import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import URLInputFile
from fakeredis.aioredis import FakeRedis

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders import tiktok_api_source
from saver_backend.services.downloaders.resolver import TikTokDetector
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.tiktok_api_source import TikTokAPIController
from saver_backend.services.telegram.bot_controller import TelegramBotController

URL = "https://www.tiktok.com/@user/video/7047596209028074758"
LIMIT = {"code": -1, "msg": "Free Api Limit: 1 request/second."}
SUCCESS = {
    "code": 0,
    "msg": "success",
    "data": {"id": "7047596209028074758", "play": "https://v/1.mp4", "cover": "c"},
}


@pytest.mark.parametrize(
    ("url", "kind", "code"),
    [
        (URL, "video", "7047596209028074758"),
        (
            "https://www.tiktok.com/@user/photo/7266832970353233185",
            "photo",
            "7266832970353233185",
        ),
        (
            "https://m.tiktok.com/v/7059698374567611694.html",
            "mobile",
            "7059698374567611694",
        ),
        ("https://vt.tiktok.com/ZS9c687pa/", "short", "ZS9c687pa"),
        ("https://www.tiktok.com/t/ZTRC5xgJp/", "short", "ZTRC5xgJp"),
    ],
)
def test_detects_tiktok_links(url: str, kind: str, code: str) -> None:
    """All TikTok share link formats are recognised."""
    resolution = TikTokDetector().match(url)
    assert resolution is not None
    assert resolution.metadata == {"code": code, "type": kind}


def _controller() -> tuple[TikTokAPIController, AsyncMock]:
    cache_dao = AsyncMock()
    cache_dao.get_by_filters.return_value = None
    telegram = AsyncMock()
    telegram.get_fsm_data.return_value = None
    controller = TikTokAPIController(
        resolution=Resolution(
            source=SourceEnum.TIKTOK,
            url=URL,
            metadata={"code": "7047596209028074758", "type": "video"},
        ),
        telegram_bot_controller=telegram,
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=cache_dao,
        message_id=1,
    )
    return controller, telegram


def _response(payload: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    return response


@pytest.mark.anyio
async def test_retries_when_tikwm_rate_limits() -> None:
    """A 'Free Api Limit' answer is retried in the next slot."""
    controller, _ = _controller()
    post = AsyncMock(side_effect=[_response(LIMIT), _response(SUCCESS)])
    with (
        patch.object(controller, "_wait_for_slot", AsyncMock()) as wait,
        patch.object(controller._client, "post", post),  # noqa: SLF001
    ):
        info = await controller.get_video_info(URL)

    assert info is not None
    assert info["id"] == "7047596209028074758"
    assert wait.await_count == 2
    await controller.close()


@pytest.mark.anyio
async def test_falls_back_to_ydl_when_tikwm_fails() -> None:
    """yt-dlp is used when tikwm gives nothing."""
    controller, telegram = _controller()
    ydl = AsyncMock()
    with (
        patch.object(controller, "get_video_info", AsyncMock(return_value=None)),
        patch.object(
            tiktok_api_source,
            "TikTokYdlController",
            return_value=ydl,
        ) as ydl_cls,
    ):
        await controller.download_video()

    ydl_cls.assert_called_once()
    ydl.download_video.assert_awaited_once()
    ydl.close.assert_awaited_once()
    telegram.send_error_downloading.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_requests_wait_for_their_turn() -> None:
    """Two requests in a row are spaced by the tikwm interval."""
    controller, _ = _controller()
    await controller._redis.aclose()  # noqa: SLF001
    controller._redis = FakeRedis()  # noqa: SLF001

    started = time.monotonic()
    await controller._wait_for_slot()  # noqa: SLF001
    await controller._wait_for_slot()  # noqa: SLF001

    assert time.monotonic() - started >= tiktok_api_source.TIKWM_INTERVAL_MS / 1000
    await controller.close()


@pytest.mark.anyio
async def test_uploads_video_when_telegram_cannot_fetch_url() -> None:
    """Videos over Telegram's URL limit are uploaded as files."""
    bot_controller = MagicMock()
    sent = MagicMock()
    bot_controller._bot.send_video = AsyncMock(  # noqa: SLF001
        side_effect=[
            TelegramBadRequest(method=MagicMock(), message="failed to get HTTP URL"),
            sent,
        ],
    )
    video = VideoDTO(url=URL, source_id="1", direct_download_url="https://v/1.mp4")

    message = await TelegramBotController._send_video_or_upload(  # noqa: SLF001
        bot_controller,
        video=video,
        video_input="https://v/1.mp4",
        chat_id=1,
    )

    assert message is sent
    retry = bot_controller._bot.send_video.await_args_list[1]  # noqa: SLF001
    assert isinstance(retry.kwargs["video"], URLInputFile)
