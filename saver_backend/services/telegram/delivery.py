from typing import TYPE_CHECKING, Any, cast

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import (
    AnswerInlineQuery,
    SendAnimation,
    SendAudio,
    SendDocument,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SendVideo,
)
from aiogram.methods.base import TelegramType

from saver_backend.services.failure_log import mark_delivered

if TYPE_CHECKING:
    from aiogram.client.bot import Bot
    from aiogram.methods import TelegramMethod

# Requests that hand the user a result: a file, the quality menu (a photo or
# a message with buttons) or an inline answer
_RESULT_METHODS = (
    SendAnimation,
    SendAudio,
    SendDocument,
    SendMediaGroup,
    SendPhoto,
    SendVideo,
    AnswerInlineQuery,
)


def _is_result(method: Any) -> bool:
    if isinstance(method, _RESULT_METHODS):
        return True
    # the quality menu without a thumbnail is a text message with buttons
    return isinstance(method, SendMessage) and method.reply_markup is not None


class DeliveryMiddleware(BaseRequestMiddleware):
    """Mark the current request as delivered once Telegram accepts a result."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: "Bot",
        method: "TelegramMethod[TelegramType]",
    ) -> TelegramType:
        """
        Send the request and mark delivery when Telegram accepted it.

        The chain returns the method's result (Message, User, ...), not a
        Response; a request Telegram refused raises instead.

        :param make_request: Next step of the request chain.
        :param bot: Bot that sends the request.
        :param method: Telegram method being sent.
        :return: Result of the method.
        """
        result = await make_request(bot, method)
        if _is_result(method):
            mark_delivered()
        return cast("TelegramType", result)
