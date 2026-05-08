from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DatabaseProviderMiddleware(BaseMiddleware):
    """Middleware that provides database session to handlers."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """
        Provide database session to handlers.

        :param handler: handler to call.
        :param event: event object.
        :param data: data dictionary.
        :return: result of the handler.
        """
        session: AsyncSession = self._session_factory()
        data["db_session"] = session
        try:
            result = await handler(event, data)
        finally:
            await session.commit()
            await session.close()
        return result
