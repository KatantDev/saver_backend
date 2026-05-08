from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser

if TYPE_CHECKING:
    from saver_backend.db.dao.user_dao import UserDAO


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
            await user_dao.update(
                telegram_id=aiogram_user.id,
                username=aiogram_user.username,
                language_code=aiogram_user.language_code or "en",
                first_name=aiogram_user.first_name,
                last_name=aiogram_user.last_name,
            )
        data["user"] = user
        return await handler(event, data)
