import asyncio
import hmac
from typing import Annotated

from fastapi import APIRouter, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from saver_backend.services.downloaders.instagram_session import (
    InstagramSessionRejectedError,
    save_pushed_session,
)
from saver_backend.settings import settings
from saver_backend.web.api.instagram.schema import (
    SessionPushRequest,
    SessionPushResponse,
)

router = APIRouter(prefix="/instagram", include_in_schema=False)
session_token_header = APIKeyHeader(name="X-Saver-Token", auto_error=False)


def validate_session_token(
    token: Annotated[str | None, Security(session_token_header)],
) -> None:
    """
    Allow only the browser extension that knows the token.

    :param token: Token from the X-Saver-Token header.
    """
    expected = settings.instagram_session_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


@router.post("/session", dependencies=[Security(validate_session_token)])
async def push_session(body: SessionPushRequest) -> SessionPushResponse:
    """
    Save the service account session sent by the browser extension.

    :param body: instagram.com cookies.
    :return: Account whose session was saved.
    """
    try:
        username = await asyncio.to_thread(save_pushed_session, body.cookies)
    except InstagramSessionRejectedError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return SessionPushResponse(username=username)
