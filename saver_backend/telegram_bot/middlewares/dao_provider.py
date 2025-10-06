from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from saver_backend.db.dao.user_dao import UserDAO


class DAOProviderMiddleware(BaseMiddleware):
    """Middleware that provides DAO instances to handlers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """
        Provide DAO instances to handlers.

        :param handler: handler to call.
        :param event: event object.
        :param data: data dictionary.
        :return: result of the handler.
        """
        session: AsyncSession = data["db_session"]
        data["user_dao"] = UserDAO(session)
        return await handler(event, data)
