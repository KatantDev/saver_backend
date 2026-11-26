from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import FSInputFile

from saver_backend.services.downloaders.instagram_indown_source import (
    InstagramInDownController,
    main_cdn_link,
)
from saver_backend.services.downloaders.schema import VideoDTO
from saver_backend.services.telegram import bot_controller as bot_controller_module
from saver_backend.services.telegram.bot_controller import (
    URL_FETCH_TIMEOUT,
    TelegramBotController,
    _telegram_could_not_fetch,
)

URL = "https://www.tiktok.com/@nasa/video/1"
PATH = "/o1/v/t2/f2/m86/AQPE3CeCINmCOK.mp4?_nc_ht=x&oe=6B077300&_nc_sid=5e9851"
CACHE = "https://instagram.fplv1-2.fna.fbcdn.net" + PATH
MAIN = "https://scontent.cdninstagram.com" + PATH


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        (CACHE, MAIN),
        ("https://instagram.fiev17-2.fna.fbcdn.net/v/t51.82787-15/a.jpg?oe=1", None),
        ("https://scontent-ams2-1.cdninstagram.com" + PATH, "same"),
        ("https://video.xx.fbcdn.net" + PATH, "same"),
        ("https://evil.example/?u=instagram.fplv1-2.fna.fbcdn.net", "same"),
        ("https://indown.io/344.jpg", "same"),
    ],
)
def test_provider_cache_link_goes_to_the_main_host(
    link: str,
    expected: str | None,
) -> None:
    """Prod 23-25.11: 21-38 files a day did not download from such caches."""
    result = main_cdn_link(link)

    if expected == "same":
        assert result == link
    elif expected is None:
        assert result.startswith("https://scontent.cdninstagram.com/v/t51.82787-15/")
        assert result.endswith("a.jpg?oe=1")
    else:
        assert result == expected


def test_links_from_indown_are_rewritten() -> None:
    """Both a plain link and one wrapped in indown.io/fetch lose the cache host."""
    source = MagicMock()
    clean = InstagramInDownController._extract_clean_url  # noqa: SLF001

    assert clean(source, CACHE.replace("&", "&amp;")) == MAIN
    wrapped = "https://indown.io/fetch?url=" + CACHE.replace("?", "%3F").replace(
        "&",
        "%26",
    )
    assert clean(source, wrapped) == MAIN


@pytest.mark.anyio
async def test_link_send_that_hangs_becomes_an_upload(tmp_path: Path) -> None:
    """Prod 25.11: Telegram did not answer for 10 minutes about a TikTok link."""
    controller = MagicMock()
    sent = MagicMock()
    hung = TelegramNetworkError(method=MagicMock(), message="Request timeout error")
    controller._bot.send_video = AsyncMock(side_effect=[hung, sent])  # noqa: SLF001
    downloaded = tmp_path / "video.mp4"
    downloaded.write_bytes(b"video")
    link = "https://v19.tiktokcdn-us.com/video.mp4"
    video = VideoDTO(url=URL, source_id="1", direct_download_url=link, duration=30)

    with (
        patch.object(
            bot_controller_module,
            "_download_to_temp_file",
            AsyncMock(return_value=downloaded),
        ) as download,
        patch.object(
            bot_controller_module,
            "_probe_video",
            AsyncMock(return_value={"width": 576, "height": 1024, "duration": 30}),
        ),
    ):
        message = await TelegramBotController._send_video_or_upload(  # noqa: SLF001
            controller,
            video=video,
            video_input=link,
            chat_id=1,
            width=576,
            height=1024,
        )

    assert message is sent
    download.assert_awaited_once_with(link)
    by_link, uploaded = controller._bot.send_video.await_args_list  # noqa: SLF001
    assert by_link.kwargs["video"] == link
    assert by_link.kwargs["request_timeout"] == URL_FETCH_TIMEOUT
    assert isinstance(uploaded.kwargs["video"], FSInputFile)


@pytest.mark.anyio
async def test_upload_of_a_local_file_keeps_its_own_time_limit(tmp_path: Path) -> None:
    """A file from the disk is not cut at the limit meant for links."""
    controller = MagicMock()
    hung = TelegramNetworkError(method=MagicMock(), message="Request timeout error")
    controller._bot.send_video = AsyncMock(side_effect=[hung])  # noqa: SLF001
    local = tmp_path / "video.mp4"
    local.write_bytes(b"video")
    video = VideoDTO(url=URL, source_id="1", path=local, width=1, height=1)

    with pytest.raises(TelegramNetworkError):
        await TelegramBotController._send_video_or_upload(  # noqa: SLF001
            controller,
            video=video,
            video_input=FSInputFile(path=local),
            chat_id=1,
            width=1,
            height=1,
            duration=1,
        )

    call = controller._bot.send_video.await_args_list[0]  # noqa: SLF001
    assert call.kwargs["request_timeout"] is None


@pytest.mark.anyio
async def test_progress_line_is_skipped_while_the_chat_is_paused() -> None:
    """The progress edit would only push the file itself further back."""
    controller = MagicMock()
    controller._bot.edit_message_text = AsyncMock()  # noqa: SLF001
    controller._flood_control.pause_left.return_value = 6.0  # noqa: SLF001

    await TelegramBotController.send_update_downloading(controller, 1, 2, 50)
    controller._bot.edit_message_text.assert_not_awaited()  # noqa: SLF001

    controller._flood_control.pause_left.return_value = 0.0  # noqa: SLF001
    with patch.object(bot_controller_module, "_", lambda text, **_kwargs: text):
        await TelegramBotController.send_update_downloading(controller, 1, 2, 50)
    controller._bot.edit_message_text.assert_awaited_once()  # noqa: SLF001


def test_wrong_http_url_counts_as_a_link_telegram_could_not_open() -> None:
    """Prod 25.11: two Instagram albums were refused with this text."""
    error = TelegramBadRequest(
        method=MagicMock(),
        message=(
            "Bad Request: failed to send message #1 with the error message"
            ' "Wrong file identifier/HTTP URL specified"'
        ),
    )
    assert _telegram_could_not_fetch(error)
    other = TelegramBadRequest(method=MagicMock(), message="Bad Request: not Found")
    assert not _telegram_could_not_fetch(other)
