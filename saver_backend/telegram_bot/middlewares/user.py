from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser
from aiogram.utils.i18n import SimpleI18nMiddleware

if TYPE_CHECKING:
    from saver_backend.db.dao.user_dao import UserDAO

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("ru", "en")


def choose_language(stored: str | None, reported: str | None) -> str:
    """
    Pick the language for the user's replies.

    Telegram sends ``language_code`` of the client that sent the update, so a
    user with a Russian phone and an English desktop or third-party client got
    answers in both languages in turn (task 10). English is also what clients
    report when they do not know the language, so it never replaces a stored
    non-English language; any other reported language does.

    :param stored: Language saved for the user.
    :param reported: ``language_code`` of the current update.
    :return: Language to save and to answer in.
    """
    if not reported:
        return stored or DEFAULT_LANGUAGE
    if reported.startswith(DEFAULT_LANGUAGE) and stored:
        return stored
    return reported


class UserMiddleware(BaseMiddleware):
    """Middleware that provides user to handlers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """
        Provide user to handlers.

        If user is not in database, create it.
        If user is in database, update it.

        :param handler: handler to call.
        :param event: event object.
        :param data: data dictionary.
        """
        aiogram_user: TgUser | None = data.get("event_from_user")
        if aiogram_user is None or aiogram_user.is_bot:
            return await handler(event, data)
        user_dao: "UserDAO" = data["user_dao"]
        user = await user_dao.get_by_id(telegram_id=aiogram_user.id)
        if user is None:
            user = await user_dao.create(
                telegram_id=aiogram_user.id,
                username=aiogram_user.username,
                language_code=aiogram_user.language_code or "en",
                first_name=aiogram_user.first_name,
                last_name=aiogram_user.last_name,
            )
        else:
            language = choose_language(user.lang, aiogram_user.language_code)
            await user_dao.update(
                telegram_id=aiogram_user.id,
                username=aiogram_user.username,
                language_code=language,
                first_name=aiogram_user.first_name,
                last_name=aiogram_user.last_name,
            )
            user.lang = language
        data["user"] = user
        controller = data.get("telegram_bot_controller")
        if controller is not None:
            controller.language = (
                user.lang if user.lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
            )
        return await handler(event, data)


class UserI18nMiddleware(SimpleI18nMiddleware):
    """Translate handler replies into the saved language of the user."""

    async def get_locale(self, event: TelegramObject, data: dict[str, Any]) -> str:
        """
        Use the language chosen by UserMiddleware, not the one of the update.

        :param event: event object.
        :param data: data dictionary.
        :return: locale for the handler.
        """
        lang = getattr(data.get("user"), "lang", None)
        if not lang:
            return await super().get_locale(event, data)
        if lang in self.i18n.available_locales:
            return lang
        return self.i18n.default_locale
