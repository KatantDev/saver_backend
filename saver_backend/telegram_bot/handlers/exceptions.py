import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent
from sentry_sdk import capture_exception

from saver_backend.settings import settings

exception_router = Router()

QUERY_TOO_OLD = "query is too old and response timeout expired or query ID is invalid"


@exception_router.error()
async def error_handler(event: ErrorEvent) -> None:
    """
    Handle exceptions.

    :param event: Error event.
    """
    exception = event.exception
    if isinstance(exception, TelegramBadRequest) and QUERY_TOO_OLD in exception.message:
        return

    if settings.environment == "local":
        logging.error(event)

    capture_exception(event.exception)
