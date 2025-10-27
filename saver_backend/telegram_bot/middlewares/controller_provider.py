from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

if TYPE_CHECKING:
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
        return await handler(event, data)
