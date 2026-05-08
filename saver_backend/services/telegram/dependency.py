from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from saver_backend.services.telegram.bot_controller import TelegramBotController
from saver_backend.services.telegram.exceptions import InvalidWebhookSecretException

webhook_secret_security = APIKeyHeader(
    name="X-Telegram-Bot-Api-Secret-Token",
    auto_error=False,
)


async def get_telegram_bot_controller(request: Request) -> TelegramBotController:
    """
    Get telegram bot controller from request.

    :param request: Request instance.
    :return: Telegram bot controller.
    """
    return request.app.state.telegram_bot_controller


def validate_secret_key(
    webhook_secret: Annotated[str | None, Security(webhook_secret_security)],
    telegram_bot_controller: Annotated[
        TelegramBotController,
        Depends(get_telegram_bot_controller),
    ],
) -> None:
    """
    Validate webhook secret key.

    :param webhook_secret: Webhook secret key.
    :param telegram_bot_controller: Telegram bot controller
    """
    if not telegram_bot_controller.is_valid_webhook_secret(webhook_secret):
        raise InvalidWebhookSecretException
