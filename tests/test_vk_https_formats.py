from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from saver_backend.services.consts import TELEGRAM_UPLOAD_LIMIT_BYTES
from saver_backend.services.downloaders.schema import FormatDTO, VideoDTO
from saver_backend.services.telegram.bot_controller import TelegramBotController

# Formats of https://vkvideo.ru/video-25904422_170190849 as yt-dlp returns them
VK_HTTPS_FORMATS = [
    {
        "format_id": f"url{height}",
        "protocol": "https",
        "resolution": f"{height}p",
        "height": height,
        "ext": "mp4",
    }
    for height in (144, 240, 360, 480, 720)
]


def test_vk_plain_https_file_is_a_quality() -> None:
    """Prod 21-23.11: videos with only url144…url720 had no quality to pick."""
    dto = FormatDTO.from_yt_dlp(VK_HTTPS_FORMATS[-1], duration=60)

    assert dto is not None
    assert dto.format_id == "url720"
    assert dto.height == 720


def test_menu_of_an_https_only_video_lists_every_quality() -> None:
    """All five qualities of such a video reach the menu."""
    video = VideoDTO.from_yt_dlp(
        info={"id": "1", "title": "t", "duration": 60, "formats": VK_HTTPS_FORMATS},
    )

    assert sorted(fmt.height for fmt in video.formats) == [144, 240, 360, 480, 720]


def test_usual_format_is_read_as_before() -> None:
    """A WxH resolution keeps working."""
    dto = FormatDTO.from_yt_dlp(
        {"format_id": "hls-1", "resolution": "1280x720", "height": 720},
        duration=60,
    )

    assert dto is not None
    assert dto.resolution == "1280x720"


def test_audio_only_format_is_still_skipped() -> None:
    """«audio only» has no height and is not a video quality."""
    assert (
        FormatDTO.from_yt_dlp(
            {"format_id": "a", "resolution": "audio only", "height": None},
            duration=60,
        )
        is None
    )


@pytest.mark.anyio
async def test_file_over_two_gigabytes_is_not_uploaded(tmp_path: Path) -> None:
    """Prod 21-23.11: 93 uploads ended with FILE_PARTS_INVALID."""
    controller = MagicMock()
    controller.send_file_too_large = AsyncMock()
    controller._send_video_or_upload = AsyncMock()  # noqa: SLF001
    path = tmp_path / "big.mp4"
    with path.open("wb") as file:
        file.truncate(TELEGRAM_UPLOAD_LIMIT_BYTES + 1)
    video = VideoDTO(url="https://vkvideo.ru/video-1_1", source_id="1", path=path)

    result = await TelegramBotController.send_finish_downloading(
        controller,
        video=video,
        telegram_id=1,
        message_id=5,
    )

    assert result is None
    controller.send_file_too_large.assert_awaited_once_with(1, 5, None)
    controller._send_video_or_upload.assert_not_awaited()  # noqa: SLF001
