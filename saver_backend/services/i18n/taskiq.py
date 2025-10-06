import contextvars
from typing import Any

from aiogram.utils.i18n import I18n
from taskiq import AsyncBroker, TaskiqMessage, TaskiqMiddleware, TaskiqResult


class I18nMiddleware(TaskiqMiddleware):
    """Middleware for setting the current I18n context."""

    def __init__(self, broker: AsyncBroker) -> None:
        super().__init__()
        self.broker = broker
        self._tokens: dict[str, contextvars.Token[I18n]] = {}

    async def pre_execute(self, message: "TaskiqMessage") -> TaskiqMessage:
        """
        Set the current I18n context.

        :param message: object with taskiq message
        :return: object with taskiq message
        """
        self._tokens[message.task_id] = I18n.set_current(self.broker.state.i18n)
        return message

    async def post_save(
        self,
        message: "TaskiqMessage",
        result: "TaskiqResult[Any]",
    ) -> None:
        """
        Reset the current I18n context.

        :param message: object with taskiq message
        :param result: object with taskiq result
        """
        token = self._tokens[message.task_id]
        if token:
            I18n.reset_current(token)
            self._tokens.pop(message.task_id)
