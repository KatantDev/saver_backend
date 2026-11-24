from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import SourceEnum
from saver_backend.entities.resolution import Resolution
from saver_backend.services.downloaders import instagram_indown_source
from saver_backend.services.downloaders.instagram_indown_source import (
    InstagramInDownController,
)
from saver_backend.services.downloaders.schema import PhotoDTO, VideoDTO

URL = "https://www.instagram.com/reel/DejbPYGM119/"
FORM_FIELDS = ["token", "https://indown.io/", "e", "e"]
EMPTY_RESPONSE = "ERROR: [Instagram] x: Instagram sent an empty media response."
RATE_LIMIT = "ERROR: [Instagram] x: HTTP Error 429: Too Many Requests"


def _controller(
    url: str = URL,
    metadata: dict[str, Any] | None = None,
) -> tuple[InstagramInDownController, AsyncMock]:
    cache_dao = AsyncMock()
    cache_dao.get_by_filters.return_value = None
    telegram = AsyncMock()
    telegram.get_fsm_data.return_value = None
    controller = InstagramInDownController(
        resolution=Resolution(
            source=SourceEnum.INSTAGRAM_INDOWN,
            url=url,
            metadata=metadata or {"code": "DejbPYGM119", "type": "reels"},
        ),
        telegram_bot_controller=telegram,
        telegram_id=1,
        user_dao=AsyncMock(),
        history_dao=AsyncMock(),
        cache_dao=cache_dao,
        message_id=1,
    )
    controller._send_video = AsyncMock()  # type: ignore[method-assign]  # noqa: SLF001
    controller._send_media_result = AsyncMock()  # type: ignore[method-assign]  # noqa: SLF001
    return controller, telegram


def _ydl(media: list[Any] | None = None, error: Exception | None = None) -> AsyncMock:
    ydl = AsyncMock()
    ydl.fetch_media = AsyncMock(return_value=media, side_effect=error)
    return ydl


def _indown_page(text: str) -> AsyncMock:
    response = AsyncMock()
    response.text = text
    response.raise_for_status = lambda: None
    return response


@pytest.mark.anyio
async def test_reel_is_read_as_a_guest_before_indown() -> None:
    """A reel a guest can see spends no indown.io quota."""
    controller, telegram = _controller()
    video = VideoDTO(source_id="x", direct_download_url="https://cdn/v.mp4")
    ydl = _ydl([video])
    form = AsyncMock(return_value=FORM_FIELDS)

    with (
        patch.object(controller, "_get_form_fields", form),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            return_value=ydl,
        ) as ydl_cls,
    ):
        await controller.download_video()

    assert ydl_cls.call_args.kwargs["use_session"] is False
    controller._send_video.assert_awaited_once_with(video)  # type: ignore[attr-defined]  # noqa: SLF001
    assert video.source_id == "DejbPYGM119"
    form.assert_not_awaited()
    ydl.close.assert_awaited_once()
    telegram.send_error_downloading.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_photo_post_is_sent_from_the_guest_answer() -> None:
    """Photos and carousels no longer wait for indown.io (142 failures in 3 days)."""
    controller, _telegram = _controller(
        url="https://www.instagram.com/p/DfIY71Lku89/",
        metadata={"code": "DfIY71Lku89", "type": "p"},
    )
    photos = [
        PhotoDTO(source_id="a", media_url="https://cdn/1.jpg"),
        PhotoDTO(source_id="b", media_url="https://cdn/2.jpg"),
    ]
    form = AsyncMock(return_value=FORM_FIELDS)

    with (
        patch.object(controller, "_get_form_fields", form),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            return_value=_ydl(photos),
        ),
    ):
        await controller.download_video()

    controller._send_media_result.assert_awaited_once_with(photos)  # type: ignore[attr-defined]  # noqa: SLF001
    controller._send_video.assert_not_awaited()  # type: ignore[attr-defined]  # noqa: SLF001
    assert [photo.source_id for photo in photos] == ["DfIY71Lku89"] * 2
    form.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_indown_is_asked_when_the_guest_request_fails() -> None:
    """A post hidden from guests still goes to indown.io."""
    controller, telegram = _controller()
    media = [VideoDTO(source_id="x", direct_download_url="https://indown/v.mp4")]

    with (
        patch.object(
            controller,
            "_get_form_fields",
            AsyncMock(return_value=FORM_FIELDS),
        ),
        patch.object(controller, "_extract_media_dtos", return_value=media),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            return_value=_ydl(error=DownloadError(EMPTY_RESPONSE)),
        ),
        patch(
            "httpx.AsyncClient.post",
            AsyncMock(return_value=_indown_page("<html>media</html>")),
        ),
    ):
        await controller.download_video()

    controller._send_media_result.assert_awaited_once_with(media)  # type: ignore[attr-defined]  # noqa: SLF001
    telegram.send_error_downloading.assert_not_awaited()
    telegram.send_content_not_found_error.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_general_error_when_indown_could_not_be_asked() -> None:
    """With the quota spent the user is not told the post is private."""
    controller, telegram = _controller()

    with (
        patch.object(controller, "_get_form_fields", AsyncMock(return_value=None)),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            return_value=_ydl(error=DownloadError(EMPTY_RESPONSE)),
        ),
    ):
        await controller.download_video()

    telegram.send_error_downloading.assert_awaited_once()
    telegram.send_content_not_found_error.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_dead_link_is_private_or_deleted() -> None:
    """Unavailable for a guest and nothing on indown.io: «private or deleted»."""
    controller, telegram = _controller()

    with (
        patch.object(
            controller,
            "_get_form_fields",
            AsyncMock(return_value=FORM_FIELDS),
        ),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            return_value=_ydl(error=DownloadError(EMPTY_RESPONSE)),
        ),
        patch(
            "httpx.AsyncClient.post",
            AsyncMock(return_value=_indown_page("<html></html>")),
        ),
    ):
        await controller.download_video()

    telegram.send_content_not_found_error.assert_awaited_once()
    telegram.send_error_downloading.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_rate_limited_guest_request_is_not_called_deleted() -> None:
    """A 429 for the guest and nothing on indown.io is a general error."""
    controller, telegram = _controller()

    with (
        patch.object(
            controller,
            "_get_form_fields",
            AsyncMock(return_value=FORM_FIELDS),
        ),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            return_value=_ydl(error=DownloadError(RATE_LIMIT)),
        ),
        patch(
            "httpx.AsyncClient.post",
            AsyncMock(return_value=_indown_page("<html></html>")),
        ),
    ):
        await controller.download_video()

    telegram.send_error_downloading.assert_awaited_once()
    telegram.send_content_not_found_error.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_indown_private_answer_is_private_or_deleted() -> None:
    """indown.io's own «Private Video» is passed to the user."""
    controller, telegram = _controller()

    with (
        patch.object(
            controller,
            "_get_form_fields",
            AsyncMock(return_value=FORM_FIELDS),
        ),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            return_value=_ydl(error=DownloadError(RATE_LIMIT)),
        ),
        patch(
            "httpx.AsyncClient.post",
            AsyncMock(return_value=_indown_page("<html>Private Video</html>")),
        ),
    ):
        await controller.download_video()

    telegram.send_content_not_found_error.assert_awaited_once()
    telegram.send_error_downloading.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_service_account_is_the_last_resort(tmp_path: Path) -> None:
    """The account session is used only after the guest request and indown.io."""
    controller, telegram = _controller()
    session_file = tmp_path / "cookies_session.txt"
    session_file.write_text("# Netscape HTTP Cookie File\n")
    video = VideoDTO(source_id="x", direct_download_url="https://cdn/v.mp4")
    guest = _ydl(error=DownloadError(EMPTY_RESPONSE))
    account = _ydl([video])

    with (
        patch.object(controller, "_get_form_fields", AsyncMock(return_value=None)),
        patch.object(instagram_indown_source, "YDL_COOKIES_FILE", session_file),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            side_effect=[guest, account],
        ) as ydl_cls,
    ):
        await controller.download_video()

    assert [c.kwargs["use_session"] for c in ydl_cls.call_args_list] == [False, True]
    controller._send_video.assert_awaited_once_with(video)  # type: ignore[attr-defined]  # noqa: SLF001
    telegram.send_error_downloading.assert_not_awaited()
    await controller.close()


@pytest.mark.anyio
async def test_login_problem_of_the_account_asks_for_a_session_check(
    tmp_path: Path,
) -> None:
    """A dead session is reported to the people who can renew it."""
    controller, telegram = _controller()
    session_file = tmp_path / "cookies_session.txt"
    session_file.write_text("# Netscape HTTP Cookie File\n")
    guest = _ydl(error=DownloadError(RATE_LIMIT))
    account = _ydl(error=DownloadError("ERROR: [Instagram] x: login required"))

    with (
        patch.object(controller, "_get_form_fields", AsyncMock(return_value=None)),
        patch.object(instagram_indown_source, "YDL_COOKIES_FILE", session_file),
        patch.object(
            instagram_indown_source,
            "InstagramYdlController",
            side_effect=[guest, account],
        ),
        patch.object(
            instagram_indown_source,
            "request_session_check",
            AsyncMock(),
        ) as check,
    ):
        await controller.download_video()

    check.assert_awaited_once()
    telegram.send_error_downloading.assert_awaited_once()
    await controller.close()


@pytest.mark.anyio
async def test_story_goes_to_indown_then_instaloader() -> None:
    """Stories skip the guest request: only indown.io or the account has them."""
    controller, _telegram = _controller(
        url="https://www.instagram.com/stories/nasa/3500000000000000000/",
        metadata={"user": "nasa", "code": "3500000000000000000", "type": "stories"},
    )
    loader = AsyncMock()

    with (
        patch.object(controller, "_get_form_fields", AsyncMock(return_value=None)),
        patch.object(
            instagram_indown_source,
            "InstagramInstaloaderController",
            return_value=loader,
        ) as loader_cls,
        patch.object(instagram_indown_source, "InstagramYdlController") as ydl_cls,
    ):
        await controller.download_video()

    loader_cls.assert_called_once()
    loader.download_video.assert_awaited_once()
    loader.close.assert_awaited_once()
    ydl_cls.assert_not_called()
    await controller.close()


@pytest.mark.anyio
async def test_file_is_asked_directly_when_the_proxy_fails(tmp_path: Path) -> None:
    """A file lost on a dead proxy is downloaded from the server's own address."""
    controller, _telegram = _controller()
    controller._download_directory = tmp_path  # noqa: SLF001
    controller._client = MagicMock()  # noqa: SLF001
    calls: list[Any] = []

    async def stream(client: Any, _url: str, file_path: Path) -> None:
        calls.append(client)
        if client is controller._client:  # noqa: SLF001
            raise TimeoutError
        file_path.write_bytes(b"data")

    with patch.object(
        InstagramInDownController,
        "_stream_to_file",
        staticmethod(stream),
    ):
        await controller._download_file("https://cdn/1.jpg", "1.jpg")  # noqa: SLF001

    assert len(calls) == 2
    assert (tmp_path / "1.jpg").read_bytes() == b"data"
    controller._client = None  # noqa: SLF001
