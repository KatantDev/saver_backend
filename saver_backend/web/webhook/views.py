import logging
from typing import Annotated, Any

from aiogram.types import Update
from fastapi import APIRouter, Depends, Request, Security, status
from pydantic import ValidationError

from saver_backend.services.telegram.bot_controller import TelegramBotController
from saver_backend.services.telegram.dependency import (
    get_telegram_bot_controller,
    validate_secret_key,
)
from saver_backend.settings import settings

webhook_router = APIRouter(include_in_schema=False)

MAX_REPORTED_FIELDS = 10


def describe_unreadable_update(body: Any, error: Exception) -> str:
    """
    What is wrong with an update we could not read, without user data.

    :param body: Parsed request body.
    :param error: Error raised while reading it.
    :return: Update id, its top-level keys and the fields that failed.
    """
    update_id = body.get("update_id") if isinstance(body, dict) else None
    keys = sorted(body) if isinstance(body, dict) else type(body).__name__
    if isinstance(error, ValidationError):
        fields = [
            "{}: {}".format(".".join(str(part) for part in item["loc"]), item["type"])
            for item in error.errors(include_input=False)[:MAX_REPORTED_FIELDS]
        ]
    else:
        fields = [type(error).__name__]
    return f"update_id={update_id} keys={keys} fields={fields}"


@webhook_router.post(
    path=settings.webhook_telegram_path + "/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Security(validate_secret_key)],
)
async def telegram_update_from_webhook(
    request: Request,
    telegram_bot_controller: Annotated[
        TelegramBotController,
        Depends(get_telegram_bot_controller),
    ],
) -> None:
    """
    Handle webhook update.

    The body is read here and not by FastAPI: an update our aiogram version
    cannot read used to be answered with 422, and the Bot API server sent the
    same update again every minute for a day (2687 of 9054 posts on 23.11).
    Such an update is logged and accepted, so it is not sent again.

    :param request: Request with the update in its body.
    :param telegram_bot_controller: Telegram bot controller.
    """
    body: Any = None
    try:
        body = await request.json()
        update = Update.model_validate(body)
    except (ValidationError, ValueError) as e:
        logging.error(
            "Telegram update skipped, cannot be read: %s",
            describe_unreadable_update(body, e),
        )
        return
    await telegram_bot_controller.feed_update(update=update)
