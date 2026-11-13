from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from saver_backend.services.downloaders.instagram_session import (
    InstagramSessionRejectedError,
)
from saver_backend.settings import settings
from saver_backend.web.api import instagram
from saver_backend.web.api.instagram import views

COOKIES = {"sessionid": "abc", "csrftoken": "token"}


@pytest.fixture
def api() -> AsyncClient:
    """Client for the Instagram API only, without the database."""
    app = FastAPI()
    app.include_router(instagram.router, prefix="/api")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_session_is_saved_with_the_right_token(
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extension with the token gets the session saved."""
    monkeypatch.setattr(settings, "instagram_session_token", "secret")
    with patch.object(views, "save_pushed_session", return_value="bot") as save:
        response = await api.post(
            "/api/instagram/session",
            json={"cookies": COOKIES},
            headers={"X-Saver-Token": "secret"},
        )

    assert response.status_code == 200
    assert response.json() == {"username": "bot"}
    save.assert_called_once_with(COOKIES)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("configured", "sent", "code"),
    [(None, "secret", 404), ("secret", None, 401), ("secret", "wrong", 401)],
    ids=["switched off", "no token", "wrong token"],
)
async def test_session_needs_the_token(
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    configured: str | None,
    sent: str | None,
    code: int,
) -> None:
    """Without the right token nothing reaches Instagram or the disk."""
    monkeypatch.setattr(settings, "instagram_session_token", configured)
    headers = {"X-Saver-Token": sent} if sent else {}
    with patch.object(views, "save_pushed_session") as save:
        response = await api.post(
            "/api/instagram/session",
            json={"cookies": COOKIES},
            headers=headers,
        )

    assert response.status_code == code
    save.assert_not_called()


@pytest.mark.anyio
async def test_rejected_session_is_reported(
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extension sees why the session was not accepted."""
    monkeypatch.setattr(settings, "instagram_session_token", "secret")
    with patch.object(
        views,
        "save_pushed_session",
        side_effect=InstagramSessionRejectedError("Instagram did not accept"),
    ):
        response = await api.post(
            "/api/instagram/session",
            json={"cookies": COOKIES},
            headers={"X-Saver-Token": "secret"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "Instagram did not accept"}
