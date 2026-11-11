import logging
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import urlparse

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.resolver import SourceResolver

if TYPE_CHECKING:
    from saver_backend.db.dao.request_log_dao import RequestLogDAO
    from saver_backend.db.models.user_model import UserModel

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class RequestLogMiddleware(BaseMiddleware):
    """
    Records every download request (host + resolved source), successful or not.

    Lets the owners see what people try to download and which sites are not
    supported yet. Only the host is stored, never the full URL. This runs
    after the DAO and user middlewares, so the request_log DAO and the user
    are already in the data.
    """

    def __init__(self) -> None:
        self._resolver = SourceResolver()

    @staticmethod
    def _host(url: str) -> str:
        if "://" not in url:
            url = "https://" + url
        try:
            return (urlparse(url).hostname or "").lower().removeprefix("www.")
        except ValueError:
            return ""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """
        Log the request, then pass the event on.

        A logging failure never blocks the download.

        :param handler: handler to call.
        :param event: event object.
        :param data: data dictionary.
        :return: result of the handler.
        """
        user: "UserModel | None" = data.get("user")
        dao: "RequestLogDAO | None" = data.get("request_log_dao")
        text = event.text if isinstance(event, Message) and event.text else ""
        if user is not None and dao is not None and text:
            resolution = self._resolver.resolve(text)
            # Only log messages that actually carry a link: a supported source,
            # or any text with a URL in it (so unsupported sites are counted too).
            if resolution.source != SourceEnum.UNSUPPORTED or _URL_RE.search(text):
                host = self._host(resolution.url)
                if host:
                    try:
                        await dao.create(
                            user_id=user.id,
                            source=resolution.source,
                            host=host,
                        )
                    except Exception as e:
                        logging.warning("Could not log a request: %s", e)
        return await handler(event, data)
