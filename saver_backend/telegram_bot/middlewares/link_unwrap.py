import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.resolver import SourceResolver
from saver_backend.services.link_unwrap import unwrap, wrapped_link


class LinkUnwrapMiddleware(BaseMiddleware):
    """
    Replace a link that only points to a video with the link to the video.

    Runs before the request log and the handlers, so the request is counted
    and handled as a request for the real video.
    """

    def __init__(self) -> None:
        self._resolver = SourceResolver()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """
        Unwrap the link of a message, then pass the event on.

        A failure here never blocks the message: it goes on as it came.

        :param handler: handler to call.
        :param event: event object.
        :param data: data dictionary.
        :return: result of the handler.
        """
        if isinstance(event, Update) and event.message and event.message.text:
            try:
                event = await self._unwrapped(event)
            except Exception as e:
                logging.warning("Link was not unwrapped: %s: %s", type(e).__name__, e)
        return await handler(event, data)

    async def _unwrapped(self, update: Update) -> Update:
        message = update.message
        if message is None or not message.text:
            return update
        link = wrapped_link(message.text)
        if not link:
            return update
        target = await unwrap(link)
        if not target:
            return update
        resolution = self._resolver.resolve(target)
        if resolution.source == SourceEnum.UNSUPPORTED:
            logging.info("Link points to a site the bot does not download: %s", target)
            return update
        logging.info("Link unwrapped to %s (%s)", target, resolution.source)
        new_message = message.model_copy(update={"text": target, "entities": None})
        new_update = update.model_copy(update={"message": new_message})
        if message.bot is not None:
            new_message.as_(message.bot)
            new_update.as_(message.bot)
        return new_update
