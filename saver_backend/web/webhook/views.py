from typing import Annotated

from aiogram.types import Update
from fastapi import APIRouter, Depends, Security, status

from saver_backend.services.telegram.bot_controller import TelegramBotController
from saver_backend.services.telegram.dependency import (
    get_telegram_bot_controller,
    validate_secret_key,
)
from saver_backend.settings import settings

webhook_router = APIRouter(include_in_schema=False)


@webhook_router.post(
    path=settings.webhook_telegram_path + "/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Security(validate_secret_key)],
)
async def telegram_update_from_webhook(
    update: Update,
    telegram_bot_controller: Annotated[
        TelegramBotController,
        Depends(get_telegram_bot_controller),
    ],
) -> None:
    """
    Handle webhook update.

    :param update: Telegram update object.
    :param telegram_bot_controller: Telegram bot controller.
    """
    await telegram_bot_controller.feed_update(update=update)
