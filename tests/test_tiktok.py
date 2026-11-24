import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, URLInputFile
from fakeredis.aioredis import FakeRedis

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders import tiktok_api_source
from saver_backend.services.downloaders.resolver import TikTokDetector
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.downloaders.tiktok_api_source import TikTokAPIController
from saver_backend.services.downloaders.tiktok_ydl_source import TikTokYdlController
from saver_backend.services.telegram import bot_controller as bot_controller_module
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
    controller._redis = FakeRedis()  # noqa: SLF001
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
@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("no answer in 8 seconds"),
        httpx.HTTPStatusError(
            "Server error '502 Bad Gateway'",
            request=MagicMock(),
            response=MagicMock(),
        ),
        httpx.ConnectError("refused"),
    ],
)
async def test_slow_or_failing_tikwm_gives_way_to_ydl(failure: Exception) -> None:
    """Prod 23.11: 502 answers were not caught, slow ones were waited for 30 s."""
    controller, _ = _controller()
    with (
        patch.object(controller, "_wait_for_slot", AsyncMock()),
        patch.object(controller._client, "post", AsyncMock(side_effect=failure)),  # noqa: SLF001
    ):
        info = await controller.get_video_info(URL)

    assert info is None
    await controller.close()


@pytest.mark.anyio
async def test_tikwm_is_left_out_after_two_failures_in_a_row() -> None:
    """While tikwm.com is down, requests go straight to yt-dlp without waiting."""
    controller, _ = _controller()
    post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    with (
        patch.object(controller, "_wait_for_slot", AsyncMock()),
        patch.object(controller._client, "post", post),  # noqa: SLF001
    ):
        assert await controller.get_video_info(URL) is None
        assert await controller.get_video_info(URL) is None
        assert post.await_count == 2
        assert await controller.get_video_info(URL) is None

    assert post.await_count == 2
    await controller.close()


@pytest.mark.anyio
async def test_one_slow_answer_does_not_switch_tikwm_off() -> None:
    """A single failure is forgotten once tikwm.com answers again."""
    controller, _ = _controller()
    post = AsyncMock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            _response(SUCCESS),
            httpx.ReadTimeout("slow"),
            _response(SUCCESS),
        ],
    )
    with (
        patch.object(controller, "_wait_for_slot", AsyncMock()),
        patch.object(controller._client, "post", post),  # noqa: SLF001
    ):
        results = [await controller.get_video_info(URL) for _ in range(4)]

    assert [result is not None for result in results] == [False, True, False, True]
    assert post.await_count == 4
    await controller.close()


@pytest.mark.anyio
async def test_ydl_fallback_prefers_h264() -> None:
    """The fallback sends the same H.264 file tikwm.com does, not H.265."""
    controller = TikTokYdlController(
        resolution=Resolution(source=SourceEnum.TIKTOK, url=URL, metadata={}),
        telegram_bot_controller=AsyncMock(),
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=AsyncMock(),
        message_id=1,
    )
    formats = [
        {"format_id": "h264_540p", "vcodec": "h264", "acodec": "aac", "height": 576},
        {
            "format_id": "bytevc1_1080p",
            "vcodec": "h265",
            "acodec": "aac",
            "height": 1080,
        },
    ]
    for fmt in formats:
        fmt.update(url="https://v/1.mp4", ext="mp4", protocol="https")

    picked = list(
        controller._yt_dlp.format_selector(  # noqa: SLF001
            {"formats": formats, "incomplete_formats": False},
        ),
    )

    assert [fmt["format_id"] for fmt in picked] == ["h264_540p"]
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
async def test_probes_local_file_when_size_missing(tmp_path: Path) -> None:
    """A local file with no width/height is probed so Telegram keeps the real size."""
    bot_controller = MagicMock()
    sent = MagicMock()
    bot_controller._bot.send_video = AsyncMock(return_value=sent)  # noqa: SLF001
    local = tmp_path / "reel.mp4"
    local.write_bytes(b"video")
    video = VideoDTO(url=URL, source_id="1", path=local)

    with patch.object(
        bot_controller_module,
        "_probe_video",
        AsyncMock(return_value={"width": 576, "height": 1024, "duration": 12}),
    ) as probe:
        message = await TelegramBotController._send_video_or_upload(  # noqa: SLF001
            bot_controller,
            video=video,
            video_input=FSInputFile(path=local),
            chat_id=1,
            width=None,
            height=None,
        )

    assert message is sent
    probe.assert_awaited_once_with(str(local))
    sent_call = bot_controller._bot.send_video.await_args_list[0]  # noqa: SLF001
    assert sent_call.kwargs["width"] == 576
    assert sent_call.kwargs["height"] == 1024
    assert sent_call.kwargs["duration"] == 12


@pytest.mark.anyio
async def test_keeps_given_size_for_local_file(tmp_path: Path) -> None:
    """A local file that already has a size is sent without an extra probe."""
    bot_controller = MagicMock()
    sent = MagicMock()
    bot_controller._bot.send_video = AsyncMock(return_value=sent)  # noqa: SLF001
    local = tmp_path / "reel.mp4"
    local.write_bytes(b"video")
    video = VideoDTO(url=URL, source_id="1", path=local, width=720, height=1280)

    with patch.object(
        bot_controller_module,
        "_probe_video",
        AsyncMock(return_value={"width": 1, "height": 1, "duration": 1}),
    ) as probe:
        await TelegramBotController._send_video_or_upload(  # noqa: SLF001
            bot_controller,
            video=video,
            video_input=FSInputFile(path=local),
            chat_id=1,
            width=720,
            height=1280,
            duration=30,
        )

    probe.assert_not_awaited()
    sent_call = bot_controller._bot.send_video.await_args_list[0]  # noqa: SLF001
    assert sent_call.kwargs["width"] == 720
    assert sent_call.kwargs["height"] == 1280


@pytest.mark.anyio
async def test_uploads_video_when_telegram_cannot_fetch_url(tmp_path: Path) -> None:
    """Videos over Telegram's URL limit are downloaded and uploaded as files."""
    bot_controller = MagicMock()
    sent = MagicMock()
    bot_controller._bot.send_video = AsyncMock(  # noqa: SLF001
        side_effect=[
            TelegramBadRequest(method=MagicMock(), message="failed to get HTTP URL"),
            sent,
        ],
    )
    video = VideoDTO(url=URL, source_id="1", direct_download_url="https://v/1.mp4")
    downloaded = tmp_path / "video.mp4"
    downloaded.write_bytes(b"video")

    with (
        patch.object(
            bot_controller_module,
            "_download_to_temp_file",
            AsyncMock(return_value=downloaded),
        ),
        patch.object(
            bot_controller_module,
            "_probe_video",
            AsyncMock(return_value={"width": 576, "height": 1024, "duration": 12}),
        ) as probe,
    ):
        message = await TelegramBotController._send_video_or_upload(  # noqa: SLF001
            bot_controller,
            video=video,
            video_input="https://v/1.mp4",
            chat_id=1,
        )

    assert message is sent
    probe.assert_awaited_once_with(str(downloaded))
    retry = bot_controller._bot.send_video.await_args_list[1]  # noqa: SLF001
    assert isinstance(retry.kwargs["video"], FSInputFile)
    assert retry.kwargs["width"] == 576
    assert retry.kwargs["height"] == 1024
    assert retry.kwargs["duration"] == 12
    assert not downloaded.exists()


@pytest.mark.anyio
async def test_falls_back_to_url_upload_when_download_fails() -> None:
    """If the file cannot be downloaded, Telegram gets the URL to stream."""
    bot_controller = MagicMock()
    sent = MagicMock()
    bot_controller._bot.send_video = AsyncMock(  # noqa: SLF001
        side_effect=[
            TelegramBadRequest(method=MagicMock(), message="failed to get HTTP URL"),
            sent,
        ],
    )
    video = VideoDTO(url=URL, source_id="1", direct_download_url="https://v/1.mp4")

    with patch.object(
        bot_controller_module,
        "_download_to_temp_file",
        AsyncMock(side_effect=OSError("disk full")),
    ):
        message = await TelegramBotController._send_video_or_upload(  # noqa: SLF001
            bot_controller,
            video=video,
            video_input="https://v/1.mp4",
            chat_id=1,
        )

    assert message is sent
    retry = bot_controller._bot.send_video.await_args_list[1]  # noqa: SLF001
    assert isinstance(retry.kwargs["video"], URLInputFile)


@pytest.mark.anyio
async def test_video_is_sent_without_cover_when_cover_url_fails(
    tmp_path: Path,
) -> None:
    """Prod 21-23.11: Telegram could not fetch the cover and refused the video."""
    bot_controller = MagicMock()
    sent = MagicMock()
    refused = TelegramBadRequest(
        method=MagicMock(),
        message="Bad Request: failed to get HTTP URL content",
    )
    bot_controller._bot.send_video = AsyncMock(  # noqa: SLF001
        side_effect=[refused, refused, sent],
    )
    video = VideoDTO(url=URL, source_id="1", direct_download_url="https://v/1.mp4")
    downloaded = tmp_path / "video.mp4"
    downloaded.write_bytes(b"video")

    with (
        patch.object(
            bot_controller_module,
            "_download_to_temp_file",
            AsyncMock(return_value=downloaded),
        ),
        patch.object(
            bot_controller_module,
            "_probe_video",
            AsyncMock(return_value={"width": 576, "height": 1024, "duration": 12}),
        ),
    ):
        message = await TelegramBotController._send_video_or_upload(  # noqa: SLF001
            bot_controller,
            video=video,
            video_input="https://v/1.mp4",
            chat_id=1,
            cover="https://p16/cover.webp",
        )

    assert message is sent
    calls = bot_controller._bot.send_video.await_args_list  # noqa: SLF001
    assert calls[1].kwargs["cover"] == "https://p16/cover.webp"
    assert isinstance(calls[2].kwargs["video"], FSInputFile)
    assert calls[2].kwargs["cover"] is None


def test_long_tiktok_description_fits_the_caption() -> None:
    """Prod 21-23.11: «message caption is too long» on 24 TikTok videos."""
    video = VideoDTO(url=URL, source_id="1", title="слово #tag " * 300)

    caption = video.title_html

    assert len(caption) < 1024 - 100
    assert "…" in caption


def test_short_title_is_kept_whole() -> None:
    """A usual title is not cut."""
    video = VideoDTO(url=URL, source_id="1", title="Короткое описание")

    assert "Короткое описание" in video.title_html
    assert "…" not in video.title_html
