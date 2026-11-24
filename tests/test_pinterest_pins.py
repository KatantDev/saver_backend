# ruff: noqa: SLF001
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yt_dlp

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.pinterest_ydl_source import (
    PinterestYdlController,
)
from saver_backend.services.downloaders.resolver import SourceResolver
from saver_backend.services.downloaders.ydl_source import YtDlpController


def _controller(tmp_path: Path) -> Any:
    controller: Any = PinterestYdlController.__new__(PinterestYdlController)
    controller._proxy = None
    controller._telegram_id = 1
    controller._message_id = 7
    controller._download_directory = tmp_path
    controller._resolution = MagicMock(url="https://www.pinterest.com/pin/1/")
    controller._telegram_bot_controller = MagicMock()
    controller._telegram_bot_controller.send_finish_downloading_photo = AsyncMock()
    controller._telegram_bot_controller.send_finish_downloading_gif = AsyncMock()
    controller._create_history_entry = AsyncMock()
    controller._send_error_message = AsyncMock()
    return controller


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("picture", "method"),
    [
        (
            "https://i.pinimg.com/originals/aa/bb/cc.jpg",
            "send_finish_downloading_photo",
        ),
        ("https://i.pinimg.com/originals/aa/bb/cc.gif", "send_finish_downloading_gif"),
    ],
)
async def test_picture_pin_is_sent_as_a_picture(
    tmp_path: Path,
    picture: str,
    method: str,
) -> None:
    """«No video formats found» was the answer to every picture pin."""
    controller = _controller(tmp_path)
    info = {
        "id": "527695281344440508",
        "formats": [],
        "thumbnails": [
            {"url": "https://i.pinimg.com/236x/aa/bb/cc.jpg"},
            {"url": picture},
        ],
    }

    async def download(url: str, path: Path) -> None:
        assert url == picture
        path.write_bytes(b"picture")

    with (
        patch.object(YtDlpController, "get_video_info", AsyncMock(return_value=info)),
        patch.object(controller, "_download_picture", download),
    ):
        result = await controller.get_video_info("https://www.pinterest.com/pin/1/")

    assert result is None
    send = getattr(controller._telegram_bot_controller, method)
    photo = send.await_args.args[0]
    assert photo.path.suffix == Path(picture).suffix
    assert send.await_args.kwargs == {"telegram_id": 1, "message_id": 7}
    assert list(tmp_path.iterdir()) == []
    controller._send_error_message.assert_not_awaited()


@pytest.mark.anyio
async def test_video_pin_goes_the_usual_way(tmp_path: Path) -> None:
    """A pin with video formats is returned for the download."""
    controller = _controller(tmp_path)
    info = {"id": "1", "formats": [{"format_id": "V_720P"}]}

    with patch.object(YtDlpController, "get_video_info", AsyncMock(return_value=info)):
        assert (
            await controller.get_video_info("https://www.pinterest.com/pin/1/") == info
        )

    controller._telegram_bot_controller.send_finish_downloading_photo.assert_not_awaited()


def test_old_pin_with_one_file_is_selected() -> None:
    """«bestvideo+bestaudio» alone fails when the pin has one file with sound."""
    formats = [
        {
            "format_id": "V_HLSV3",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "height": 1142,
            "url": "https://v/1.mp4",
            "protocol": "https",
        },
    ]
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        selector = ydl.build_format_selector("bestvideo+bestaudio/best")
        chosen = list(selector({"formats": formats, "incomplete_formats": False}))
    assert chosen[0]["format_id"] == "V_HLSV3"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.pinterest.com/pin/527695281344440508/",
            "https://www.pinterest.com/pin/527695281344440508/",
        ),
        (
            "https://ru.pinterest.com/pin/some-nice-title--527695281344440508/?a=1",
            "https://www.pinterest.com/pin/527695281344440508/",
        ),
        (
            "https://www.pinterest.co.uk/pin/527695281344440508",
            "https://www.pinterest.com/pin/527695281344440508/",
        ),
        (
            "https://pinterest.ru/pin/527695281344440508/",
            "https://www.pinterest.com/pin/527695281344440508/",
        ),
        ("https://pin.it/4Ab1cDeFg", "https://pin.it/4Ab1cDeFg"),
    ],
)
def test_pin_links_of_every_shape_are_recognised(url: str, expected: str) -> None:
    """Country domains and links with a title lead to the same pin."""
    resolution = SourceResolver().resolve(url)
    assert resolution.source == SourceEnum.PINTEREST_YDL
    assert resolution.url == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.pinterest.com/nasa/",
        "https://www.pinterest.com/nasa/space/",
        "https://notpinterest.example.com/pin/1/",
    ],
)
def test_profiles_and_boards_are_not_pins(url: str) -> None:
    """A profile used to be taken for a short link and failed in yt-dlp."""
    assert SourceResolver().resolve(url).source != SourceEnum.PINTEREST_YDL
