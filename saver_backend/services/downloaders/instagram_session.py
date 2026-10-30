import asyncio
import base64
import hashlib
import hmac
import logging
import struct
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import instaloader
from instaloader.exceptions import LoginException, TwoFactorAuthRequiredException

from saver_backend.entities.enums import SourceEnum
from saver_backend.settings import settings

COOKIES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "cookies"
# yt-dlp picks a random cookies*.txt from its folder, so this file is used as is
YDL_COOKIES_FILE = COOKIES_DIR / SourceEnum.INSTAGRAM_YDL.value / "cookies_session.txt"
LAST_LOGIN_FILE = COOKIES_DIR / SourceEnum.INSTAGRAM_INSTALOADER.value / ".last_login"
LAST_CHECK_REQUEST_FILE = (
    COOKIES_DIR / SourceEnum.INSTAGRAM_INSTALOADER.value / ".last_check_request"
)
# Logging in from a server IP too often gets the account flagged
MIN_LOGIN_INTERVAL = 60 * 60
MIN_CHECK_REQUEST_INTERVAL = 10 * 60
LOGIN_REQUIRED_HINTS = ("login required", "log in", "empty media response")


class InstagramSessionError(Exception):
    """The session cannot be restored without a person (checkpoint, bad password)."""


def session_path(login: str) -> Path:
    """
    Path of the instaloader session file for the account.

    :param login: Instagram login.
    :return: Path to the session file.
    """
    return COOKIES_DIR / SourceEnum.INSTAGRAM_INSTALOADER.value / f"{login}.session"


def totp_code(secret: str, timestamp: float | None = None) -> str:
    """
    Current 6-digit code for an authenticator app secret (RFC 6238).

    :param secret: Base32 secret shown by Instagram when 2FA is set up.
    :param timestamp: Unix time, defaults to now.
    :return: 6-digit code.
    """
    secret = secret.replace(" ", "").upper()
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((time.time() if timestamp is None else timestamp) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def _credentials() -> tuple[str, str] | None:
    login, _, password = settings.instagram_account.partition(":")
    if not login or not password or login == "username":
        return None
    return login, password


def _new_loader() -> instaloader.Instaloader:
    return instaloader.Instaloader(
        quiet=True,
        max_connection_attempts=1,
        fatal_status_codes=[400, 403, 429],
    )


def _login_allowed() -> bool:
    if not LAST_LOGIN_FILE.exists():
        return True
    return time.time() - LAST_LOGIN_FILE.stat().st_mtime >= MIN_LOGIN_INTERVAL


def _login(loader: instaloader.Instaloader, login: str, password: str) -> None:
    LAST_LOGIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_LOGIN_FILE.touch()
    try:
        loader.login(login, password)
    except TwoFactorAuthRequiredException as error:
        if not settings.instagram_totp_secret:
            raise InstagramSessionError(
                "two-factor code required, "
                "SAVER_BACKEND_INSTAGRAM_TOTP_SECRET is not set",
            ) from error
        try:
            loader.two_factor_login(totp_code(settings.instagram_totp_secret))
        except LoginException as login_error:
            raise InstagramSessionError(str(login_error)) from login_error
    except LoginException as error:
        raise InstagramSessionError(str(error)) from error


def _export_ydl_cookies(loader: instaloader.Instaloader) -> None:
    jar = MozillaCookieJar()
    for cookie in loader.context._session.cookies:  # noqa: SLF001
        jar.set_cookie(cookie)
    YDL_COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    jar.save(str(YDL_COOKIES_FILE), ignore_discard=True, ignore_expires=True)


def refresh_session() -> bool:
    """
    Make sure the service account is logged in, logging in again if needed.

    The session is shared by instaloader (session file) and yt-dlp (cookies file).

    :raises InstagramSessionError: when a person has to step in.
    :return: True if a valid session is in place.
    """
    credentials = _credentials()
    if credentials is None:
        logging.info("Instagram account is not configured, skipping session refresh")
        return False
    login, password = credentials
    path = session_path(login)
    loader = _new_loader()
    try:
        if path.exists():
            loader.load_session_from_file(username=login, filename=str(path))
            if loader.test_login() == login:
                _export_ydl_cookies(loader)
                return True
            logging.warning("Instagram session for %s has expired", login)

        if not _login_allowed():
            logging.warning(
                "Instagram login for %s skipped: tried less than an hour ago", login
            )
            return False

        logging.info("Logging in to Instagram as %s", login)
        _login(loader, login, password)
        path.parent.mkdir(parents=True, exist_ok=True)
        loader.save_session_to_file(str(path))
        _export_ydl_cookies(loader)
        logging.info("Instagram session for %s renewed", login)
        return True
    finally:
        loader.close()


def is_login_required_error(error: BaseException) -> bool:
    """
    Whether a download error means Instagram wants a logged-in session.

    :param error: Error raised by the downloader.
    :return: True if the session should be checked.
    """
    message = str(error).lower()
    return any(hint in message for hint in LOGIN_REQUIRED_HINTS)


async def request_session_check() -> None:
    """Queue a session check, at most once per MIN_CHECK_REQUEST_INTERVAL."""
    from saver_backend.task_manager.tasks import refresh_instagram_session_task

    if (
        LAST_CHECK_REQUEST_FILE.exists()
        and time.time() - LAST_CHECK_REQUEST_FILE.stat().st_mtime
        < MIN_CHECK_REQUEST_INTERVAL
    ):
        return
    LAST_CHECK_REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_CHECK_REQUEST_FILE.touch()
    await refresh_instagram_session_task.kiq()


async def refresh_instagram_session() -> bool:
    """
    Async wrapper around :func:`refresh_session`.

    :return: True if a valid session is in place.
    """
    return await asyncio.to_thread(refresh_session)
