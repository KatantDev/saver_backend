import logging
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import ffmpeg
import pytest
from aiogram.types import FSInputFile, URLInputFile, Video

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.telegram import bot_controller
from saver_backend.services.telegram.bot_controller import (
    TelegramBotController,
    _fill_video_meta,
    _probe_video,
)
from saver_backend.services.telegram.video_meta import (
    BrokenCachedVideoError,
    is_broken_video,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)


def _make_video(path: Path, size: str = "576x1024", seconds: int = 2) -> Path:
    (
        ffmpeg.input(f"color=c=black:s={size}:d={seconds}", f="lavfi")
        .output(str(path), pix_fmt="yuv420p")
        .run(quiet=True)
    )
    return path


def _video(duration: int) -> Video:
    return Video(
        file_id="f",
        file_unique_id="u",
        width=320,
        height=320,
        duration=duration,
    )


@needs_ffmpeg
@pytest.mark.anyio
async def test_probe_reads_size_and_duration(tmp_path: Path) -> None:
    """Frame size and duration come from one ffprobe call."""
    video = _make_video(tmp_path / "v.mp4")

    assert await _probe_video(str(video)) == {
        "width": 576,
        "height": 1024,
        "duration": 2,
    }


@needs_ffmpeg
@pytest.mark.anyio
async def test_fill_meta_adds_missing_size_and_duration(tmp_path: Path) -> None:
    """A reel from indown gets its real size and duration before the upload."""
    video = _make_video(tmp_path / "v.mp4")
    kwargs = {"width": None, "height": None, "duration": None, "chat_id": 1}

    await _fill_video_meta(str(video), kwargs)

    assert (kwargs["width"], kwargs["height"], kwargs["duration"]) == (576, 1024, 2)
    assert kwargs["chat_id"] == 1


@needs_ffmpeg
@pytest.mark.anyio
async def test_fill_meta_keeps_values_from_the_source(tmp_path: Path) -> None:
    """Size given by the source is kept, only the missing duration is added."""
    video = _make_video(tmp_path / "v.mp4")
    kwargs = {"width": 720, "height": 1280, "duration": None}

    await _fill_video_meta(str(video), kwargs)

    assert (kwargs["width"], kwargs["height"], kwargs["duration"]) == (720, 1280, 2)


@needs_ffmpeg
@pytest.mark.anyio
async def test_fill_meta_logs_unreadable_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A file ffprobe cannot read is reported instead of silently sent as a square."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    kwargs: dict[str, object] = {"width": None, "height": None}

    with caplog.at_level(logging.WARNING):
        await _fill_video_meta(str(broken), kwargs)

    assert kwargs == {"width": None, "height": None}
    assert "without frame size" in caplog.text


@pytest.mark.parametrize(
    ("video", "broken"),
    [(_video(0), True), (_video(12), False), (None, False)],
)
def test_broken_video_is_one_without_duration(
    video: Video | None,
    broken: bool,
) -> None:
    """Telegram marks a file it got without metadata with a zero duration."""
    assert is_broken_video(video) is broken


@pytest.mark.anyio
async def test_broken_video_is_not_cached() -> None:
    """A square without duration is not saved, so the next user gets a new upload."""
    source = MagicMock()
    source.SOURCE = SourceEnum.INSTAGRAM_INDOWN
    dto = VideoDTO(url="https://www.instagram.com/reel/x/", source_id="x")

    cached = await BaseSourceController._save_content_to_cache(  # noqa: SLF001
        source,
        dto,
        _video(0),
    )

    assert cached is None
    source._cache_dao.update_or_create.assert_not_called()  # noqa: SLF001
    source._cache_dao.create.assert_not_called()  # noqa: SLF001


@pytest.mark.anyio
async def test_broken_cached_video_is_dropped_and_downloaded_again() -> None:
    """A square saved in the cache before the fix is removed and re-downloaded."""
    source = MagicMock()
    source.SOURCE = SourceEnum.INSTAGRAM_INDOWN
    source._inline_query_id = None  # noqa: SLF001
    source._telegram_id = 1  # noqa: SLF001
    cached = MagicMock()
    cached.file_id = "square-file-id"
    cached.meta_data_dto = VideoDTO(
        url="https://www.instagram.com/reel/x/", source_id="x"
    )
    source._cache_dao.get_by_filters = AsyncMock(return_value=cached)  # noqa: SLF001
    source._cache_dao.delete_by_file_id = AsyncMock()  # noqa: SLF001
    source._telegram_bot_controller.send_video_by_file_id = AsyncMock(  # noqa: SLF001
        side_effect=BrokenCachedVideoError("x"),
    )
    source._create_history_entry = AsyncMock()  # noqa: SLF001
    source.delete_processing_message = AsyncMock()

    sent = await BaseSourceController.send_video_from_cache(source, "x", "best")

    assert sent is False
    source._cache_dao.delete_by_file_id.assert_awaited_once_with(  # noqa: SLF001
        source=SourceEnum.INSTAGRAM_INDOWN,
        file_id="square-file-id",
    )
    source._create_history_entry.assert_not_awaited()  # noqa: SLF001


@pytest.mark.anyio
@pytest.mark.parametrize("duration", [0, 101])
async def test_cached_send_takes_back_a_square(duration: int) -> None:
    """A cached square is deleted from the chat and reported; a good file is kept."""
    controller = MagicMock()
    message = MagicMock()
    message.message_id = 55
    message.video = _video(duration)
    controller._bot.send_video = AsyncMock(return_value=message)  # noqa: SLF001
    controller._send = AsyncMock()  # noqa: SLF001
    controller.language = "en"
    cache_item = MagicMock()
    cache_item.file_id = "file-id"
    cache_item.source_id = "x"
    cache_item.meta_data_dto = VideoDTO(
        url="https://www.instagram.com/reel/x/", source_id="x"
    )

    with patch.object(bot_controller, "_", lambda *_a, **_k: "{url}{title}"):
        if duration:
            video = await TelegramBotController.send_video_by_file_id(
                controller,
                telegram_id=1,
                cache_item=cache_item,
            )
            assert video is message.video
            controller._send.assert_not_awaited()  # noqa: SLF001
        else:
            with pytest.raises(BrokenCachedVideoError):
                await TelegramBotController.send_video_by_file_id(
                    controller,
                    telegram_id=1,
                    cache_item=cache_item,
                )
            controller._send.assert_awaited_once()  # noqa: SLF001
            controller._bot.delete_message.assert_called_once_with(  # noqa: SLF001
                chat_id=1,
                message_id=55,
            )


@pytest.mark.anyio
async def test_url_video_without_size_is_uploaded_with_meta(tmp_path: Path) -> None:
    """A reel indown could not save is downloaded here and sent with its size."""
    controller = MagicMock()
    controller._upload_with_meta = (  # noqa: SLF001
        lambda *a: TelegramBotController._upload_with_meta(controller, *a)  # noqa: SLF001
    )
    sent = MagicMock()
    controller._bot.send_video = AsyncMock(return_value=sent)  # noqa: SLF001
    downloaded = tmp_path / "reel.mp4"
    downloaded.write_bytes(b"video")
    video = VideoDTO(
        url="https://www.instagram.com/reel/x/",
        source_id="x",
        direct_download_url="https://cdn/x.mp4",
    )

    with (
        patch.object(
            bot_controller, "_download_to_temp_file", AsyncMock(return_value=downloaded)
        ),
        patch.object(
            bot_controller,
            "_probe_video",
            AsyncMock(return_value={"width": 720, "height": 1280, "duration": 101}),
        ),
    ):
        message = await TelegramBotController._send_video_or_upload(  # noqa: SLF001
            controller,
            video=video,
            video_input=URLInputFile(url="https://cdn/x.mp4", filename="x"),
            chat_id=1,
            width=None,
            height=None,
            duration=None,
        )

    assert message is sent
    call = controller._bot.send_video.await_args_list[0]  # noqa: SLF001
    assert isinstance(call.kwargs["video"], FSInputFile)
    assert (call.kwargs["width"], call.kwargs["height"], call.kwargs["duration"]) == (
        720,
        1280,
        101,
    )
    assert not downloaded.exists()


@pytest.mark.anyio
async def test_url_video_falls_back_to_url_when_download_fails() -> None:
    """If the file cannot be fetched, the URL still goes to Telegram as before."""
    controller = MagicMock()
    controller._upload_with_meta = (  # noqa: SLF001
        lambda *a: TelegramBotController._upload_with_meta(controller, *a)  # noqa: SLF001
    )
    sent = MagicMock()
    controller._bot.send_video = AsyncMock(return_value=sent)  # noqa: SLF001
    video = VideoDTO(
        url="https://www.instagram.com/reel/x/",
        source_id="x",
        direct_download_url="https://cdn/x.mp4",
    )
    url_input = URLInputFile(url="https://cdn/x.mp4", filename="x")

    with patch.object(
        bot_controller, "_download_to_temp_file", AsyncMock(side_effect=OSError("403"))
    ):
        message = await TelegramBotController._send_video_or_upload(  # noqa: SLF001
            controller,
            video=video,
            video_input=url_input,
            chat_id=1,
            width=None,
            height=None,
        )

    assert message is sent
    assert controller._bot.send_video.await_args_list[0].kwargs["video"] is url_input  # noqa: SLF001
