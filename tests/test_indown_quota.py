import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from saver_backend.services.downloaders import instagram_indown_source as indown
from saver_backend.services.downloaders.instagram_indown_source import (
    InstagramInDownController,
)

PROXIES = [f"http://p{n}" for n in range(12)]


def _controller(exhausted: dict[str, float]) -> MagicMock:
    source = MagicMock()
    source._telegram_bot_controller.get_fsm_data = AsyncMock(return_value=exhausted)  # noqa: SLF001
    source._exhausted_proxies = lambda: InstagramInDownController._exhausted_proxies(  # noqa: SLF001
        source
    )
    return source


@pytest.mark.anyio
async def test_spent_proxies_are_skipped() -> None:
    """Prod 21.11: 9 of 12 proxies over indown's 50-a-day limit."""
    spent = {indown._proxy_key(p): time.time() + 3600 for p in PROXIES[:9]}  # noqa: SLF001
    source = _controller(spent)

    with (
        patch.object(indown.settings, "proxies", PROXIES[:7]),
        patch.object(indown.settings, "proxies_ru", PROXIES[7:]),
    ):
        routes = await InstagramInDownController._indown_routes(source)  # noqa: SLF001

    assert sorted(routes) == sorted(PROXIES[9:])


@pytest.mark.anyio
async def test_spent_mark_expires() -> None:
    """A proxy is tried again once the pause is over."""
    source = _controller({indown._proxy_key(PROXIES[0]): time.time() - 1})  # noqa: SLF001

    with (
        patch.object(indown.settings, "proxies", PROXIES[:1]),
        patch.object(indown.settings, "proxies_ru", []),
    ):
        routes = await InstagramInDownController._indown_routes(source)  # noqa: SLF001

    assert routes == PROXIES[:1]


@pytest.mark.anyio
async def test_limit_page_moves_to_the_next_proxy() -> None:
    """«Too many requests» marks the proxy and the next one is used."""
    source = MagicMock()
    source._resolution.url = "https://www.instagram.com/p/DfIY71Lku89/"  # noqa: SLF001
    source._extract_source_id.return_value = "DfIY71Lku89"  # noqa: SLF001
    source._normalize_url.side_effect = lambda u: u  # noqa: SLF001
    source.BASE_URL = "https://indown.io"
    source.send_video_from_cache = AsyncMock(return_value=False)
    source._set_proxy_type_from_fsm = AsyncMock()  # noqa: SLF001
    source._indown_routes = AsyncMock(return_value=["http://spent", "http://fresh"])  # noqa: SLF001
    source._try_indown = lambda proxy, url, sid: InstagramInDownController._try_indown(  # noqa: SLF001
        source, proxy, url, sid
    )
    source._get_form_fields = AsyncMock(return_value=["t", "r", "z", "z"])  # noqa: SLF001
    source._mark_exhausted = AsyncMock()  # noqa: SLF001
    source._send_media_result = AsyncMock()  # noqa: SLF001
    source._fallback = AsyncMock()  # noqa: SLF001
    pages = iter(["Too many requests You can download maximum 50", "<media/>"])
    clients: list[MagicMock] = []

    async def init(proxy: str | None) -> None:
        client = MagicMock()
        response = MagicMock(text=next(pages))
        client.post = AsyncMock(return_value=response)
        source._client = client  # noqa: SLF001
        clients.append(client)

    source._init_httpx = AsyncMock(side_effect=init)  # noqa: SLF001
    source._extract_media_dtos.side_effect = (  # noqa: SLF001
        lambda html, *_: [MagicMock()] if "media" in html else []
    )

    await InstagramInDownController.download_video(source)

    source._mark_exhausted.assert_awaited_once_with("http://spent")  # noqa: SLF001
    source._send_media_result.assert_awaited_once()  # noqa: SLF001
    source._fallback.assert_not_awaited()  # noqa: SLF001
