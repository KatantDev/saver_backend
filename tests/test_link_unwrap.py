from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from aiogram.types import Chat, Message, Update

from saver_backend.services import link_unwrap
from saver_backend.services.link_unwrap import unwrap, wrapped_link
from saver_backend.telegram_bot.middlewares import link_unwrap as middleware_module
from saver_backend.telegram_bot.middlewares.link_unwrap import LinkUnwrapMiddleware

YANDEX = "https://yandex.ru/video/preview/12351958289685876736"
YANDEX_TOUCH = "https://yandex.kz/video/touch/preview/17231410149701787393?text=a"
SHARE = "https://share.google/NthyacRiR5oHsUKqAN"


@pytest.mark.parametrize(
    ("text", "link"),
    [
        (YANDEX, YANDEX),
        (f"look {YANDEX_TOUCH}", YANDEX_TOUCH),
        ("https://ya.ru/video/preview/123", "https://ya.ru/video/preview/123"),
        (SHARE, SHARE),
        ("https://yandex.ru/search/?text=video", None),
        ("https://youtu.be/mztYUsX_3PQ", None),
        ("no link here", None),
    ],
)
def test_only_wrapping_links_are_picked(text: str, link: str | None) -> None:
    """Ordinary links are left alone and cost no extra request."""
    assert wrapped_link(text) == link


def _yandex_session(text: str) -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    response = MagicMock(text=text, status_code=200, url=YANDEX)
    session.get = AsyncMock(return_value=response)
    return session


@pytest.mark.anyio
async def test_yandex_video_gives_the_address_of_the_video() -> None:
    """The page names the video it shows, with escaped slashes."""
    page = '..."videoUrl":"http:\\/\\/www.youtube.com\\/watch?v=mztYUsX_3PQ",...'
    with patch.object(link_unwrap, "AsyncSession", return_value=_yandex_session(page)):
        assert await unwrap(YANDEX) == "http://www.youtube.com/watch?v=mztYUsX_3PQ"


@pytest.mark.anyio
async def test_captcha_page_gives_nothing() -> None:
    """Without the video link the message goes on as it came."""
    with patch.object(
        link_unwrap,
        "AsyncSession",
        return_value=_yandex_session("<html>captcha</html>"),
    ):
        assert await unwrap(YANDEX) is None


@pytest.mark.anyio
async def test_share_google_stops_at_the_first_address_outside_google() -> None:
    """The target itself is never requested from the server."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.host == "share.google":
            return httpx.Response(
                302,
                headers={"location": "https://www.google.com/share.google?q=abc"},
            )
        return httpx.Response(302, headers={"location": "https://ok.ru/video/123"})

    real_client = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    with patch.object(link_unwrap.httpx, "AsyncClient", client):
        assert await unwrap(SHARE) == "https://ok.ru/video/123"

    assert asked == [SHARE, "https://www.google.com/share.google?q=abc"]


@pytest.mark.anyio
async def test_other_links_are_not_requested() -> None:
    """unwrap() refuses anything but the two known shapes."""
    assert await unwrap("https://example.com/video/preview/1") is None


def _update(text: str) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=1763900000,
            chat=Chat(id=5, type="private"),
            text=text,
        ),
    )


@pytest.mark.anyio
async def test_message_text_is_replaced_with_the_video_link() -> None:
    """Handlers and the request log see the real video."""
    handler = AsyncMock()
    target = "http://www.youtube.com/watch?v=mztYUsX_3PQ"

    with patch.object(middleware_module, "unwrap", AsyncMock(return_value=target)):
        await LinkUnwrapMiddleware()(handler, _update(YANDEX), {})

    passed = handler.await_args.args[0]
    assert passed.message.text == target


@pytest.mark.anyio
@pytest.mark.parametrize("target", [None, "https://example.com/page"])
async def test_message_is_kept_when_there_is_nothing_to_download(
    target: str | None,
) -> None:
    """No video behind the link or an unknown site: the message is not touched."""
    handler = AsyncMock()

    with patch.object(middleware_module, "unwrap", AsyncMock(return_value=target)):
        await LinkUnwrapMiddleware()(handler, _update(YANDEX), {})

    assert handler.await_args.args[0].message.text == YANDEX


@pytest.mark.anyio
async def test_failure_does_not_block_the_message() -> None:
    """Any error while unwrapping leaves the message as it came."""
    handler = AsyncMock()

    with patch.object(
        middleware_module,
        "unwrap",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await LinkUnwrapMiddleware()(handler, _update(YANDEX), {})

    assert handler.await_args.args[0].message.text == YANDEX


@pytest.mark.anyio
async def test_ordinary_message_costs_nothing() -> None:
    """A normal link is passed on without any request."""
    handler = AsyncMock()
    update = _update("https://youtu.be/mztYUsX_3PQ")

    with patch.object(middleware_module, "unwrap", AsyncMock()) as unwrap_mock:
        await LinkUnwrapMiddleware()(handler, update, {})

    unwrap_mock.assert_not_awaited()
    assert handler.await_args.args[0] is update


@pytest.mark.anyio
async def test_yandex_page_is_asked_through_a_proxy_after_a_timeout() -> None:
    """24-25.11: 10 of 29 links timed out from the server's own address."""
    from curl_cffi.requests.exceptions import Timeout

    page = '"videoUrl":"https:\\/\\/rutube.ru\\/video\\/abc\\/"'
    used: list[str | None] = []

    def session(**kwargs: Any) -> MagicMock:
        used.append(kwargs.get("proxy"))
        made = _yandex_session(page)
        if kwargs.get("proxy") is None:
            made.get = AsyncMock(side_effect=Timeout("slow"))
        return made

    with (
        patch.object(link_unwrap, "AsyncSession", session),
        patch.object(link_unwrap.settings, "proxies_ru", ["socks5://ru"]),
        patch.object(link_unwrap.settings, "proxies", []),
    ):
        assert await unwrap(YANDEX) == "https://rutube.ru/video/abc/"

    assert used == [None, "socks5://ru"]
