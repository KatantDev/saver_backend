from aiogram.utils.i18n import I18n
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket


class I18nMiddleware:
    """I18n middleware for setting the current I18n context."""

    def __init__(self, app: ASGIApp, i18n: I18n) -> None:
        self.app = app
        self.i18n = i18n

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """
        Set the current I18n context.

        :param scope: ASGI scope
        :param receive: callable to receive messages
        :param send: callable to send messages
        """
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        request: Request | WebSocket

        if scope["type"] == "http":
            request = Request(scope, receive=receive, send=send)
        else:
            request = WebSocket(scope, receive, send)

        lang = request.headers.get("lang", "en")
        with self.i18n.context(), self.i18n.use_locale(lang):
            return await self.app(scope, receive, send)
