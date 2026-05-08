from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from saver_backend.services.telegram.daily_report_service import DailyReportService

if TYPE_CHECKING:
    from saver_backend.db.dao.history_dao import HistoryDAO
    from saver_backend.db.dao.user_dao import UserDAO
    from saver_backend.services.telegram.bot_controller import TelegramBotController


class ControllerProviderMiddleware(BaseMiddleware):
    """Middleware that provides TelegramBotController instance to handlers."""

    def __init__(self, controller: "TelegramBotController") -> None:
        self.controller = controller

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """
        Provide controller instances to handlers.

        :param handler: handler to call.
        :param event: event object.
        :param data: data dictionary.
        :return: result of the handler.
        """
        data["telegram_bot_controller"] = self.controller
        event_from_user: Optional[User] = data.get("event_from_user")
        if event_from_user is None or event_from_user.language_code is None:
            self.controller.language = "en"
        else:
            self.controller.language = event_from_user.language_code

        user_dao: "UserDAO" = data["user_dao"]
        history_dao: "HistoryDAO" = data["history_dao"]
        data["daily_report_service"] = DailyReportService(
            user_dao=user_dao,
            history_dao=history_dao,
        )
        return await handler(event, data)
