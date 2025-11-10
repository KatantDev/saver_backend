from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from saver_backend.db.dao.history_dao import HistoryDAO
from saver_backend.db.dao.user_dao import UserDAO
from saver_backend.services.telegram.daily_report_service import DailyReportService


class ServiceProviderMiddleware(BaseMiddleware):
    """Middleware that provides service instances to handlers."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """
        Provide service instances to handlers.

        :param handler: handler to call.
        :param event: event object.
        :param data: data dictionary.
        :return: result of the handler.
        """
        user_dao: UserDAO = data["user_dao"]
        history_dao: HistoryDAO = data["history_dao"]
        data["daily_report_service"] = DailyReportService(
            user_dao=user_dao,
            history_dao=history_dao,
        )
        return await handler(event, data)
