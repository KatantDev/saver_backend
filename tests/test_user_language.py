from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.utils.i18n import I18n

from saver_backend.telegram_bot.middlewares.user import (
    UserI18nMiddleware,
    UserMiddleware,
    choose_language,
)


@pytest.mark.parametrize(
    ("stored", "reported", "expected"),
    [
        ("ru", "en", "ru"),
        ("ru", None, "ru"),
        ("ru", "", "ru"),
        ("en", "ru", "ru"),
        ("ru", "uk", "uk"),
        (None, "en", "en"),
        (None, None, "en"),
        ("en", "en", "en"),
    ],
)
def test_choose_language(
    stored: str | None, reported: str | None, expected: str
) -> None:
    """English or a missing code never replaces a saved language."""
    assert choose_language(stored, reported) == expected


async def _handle(stored: str, reported: str | None) -> tuple[MagicMock, MagicMock]:
    user = SimpleNamespace(lang=stored)
    user_dao = MagicMock()
    user_dao.get_by_id = AsyncMock(return_value=user)
    user_dao.update = AsyncMock()
    controller = MagicMock()
    data = {
        "event_from_user": SimpleNamespace(
            id=1,
            is_bot=False,
            username="u",
            language_code=reported,
            first_name="f",
            last_name=None,
        ),
        "user_dao": user_dao,
        "telegram_bot_controller": controller,
    }
    handler = AsyncMock()
    await UserMiddleware()(handler, MagicMock(), data)
    return user_dao, controller


@pytest.mark.anyio
async def test_english_client_keeps_russian_user_russian() -> None:
    """Task 10: an update from an English client does not switch the user."""
    user_dao, controller = await _handle("ru", "en")

    assert user_dao.update.await_args.kwargs["language_code"] == "ru"
    assert controller.language == "ru"


@pytest.mark.anyio
async def test_unsupported_language_answers_in_english() -> None:
    """A saved language without translations falls back to English replies."""
    user_dao, controller = await _handle("en", "uk")

    assert user_dao.update.await_args.kwargs["language_code"] == "uk"
    assert controller.language == "en"


@pytest.mark.anyio
async def test_handler_replies_use_saved_language() -> None:
    """Replies like «Getting video details» follow the saved language too."""
    i18n = MagicMock(spec=I18n, available_locales=("en", "ru"), default_locale="en")
    middleware = UserI18nMiddleware(i18n)
    english_update = {"event_from_user": SimpleNamespace(language_code="en")}

    ru = await middleware.get_locale(
        MagicMock(), {**english_update, "user": SimpleNamespace(lang="ru")}
    )
    uk = await middleware.get_locale(
        MagicMock(), {**english_update, "user": SimpleNamespace(lang="uk")}
    )

    assert ru == "ru"
    assert uk == "en"
