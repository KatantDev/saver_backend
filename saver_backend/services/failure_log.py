"""
Remember that the user got an error while a request was handled.

Every message that tells the user about an error marks the current request as
failed; the task that handled it then writes the full link to failed_request.
Controllers often catch an exception themselves and only send the error
message, so marking at the message is the one place that sees every failure.

Each taskiq task runs in its own asyncio task, so a context variable keeps the
mark of one request apart from the others handled at the same time.
"""

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlparse

MAX_ERROR_LENGTH = 1000


@dataclass
class FailureMark:
    """What went wrong while one request was handled."""

    reason: str | None = None
    error: str | None = None
    delivered: bool = False


_CURRENT: ContextVar[FailureMark | None] = ContextVar("failure_mark", default=None)


def start() -> FailureMark:
    """
    Start tracking failures for the request handled in this context.

    :return: The mark that the error messages will fill.
    """
    _install_handler()
    mark = FailureMark()
    _CURRENT.set(mark)
    return mark


def mark_failed(reason: str) -> None:
    """
    Record that the user got an error; the first reason wins.

    :param reason: Kind of the error shown to the user.
    """
    current = _CURRENT.get()
    if current is not None and current.reason is None:
        current.reason = reason


def mark_delivered() -> None:
    """Record that Telegram accepted a result for the user (file, menu, answer)."""
    current = _CURRENT.get()
    if current is not None:
        current.delivered = True


def outcome(mark: FailureMark) -> str | None:
    """
    Reason to record for a finished request, None when it went fine.

    A request that showed no error and delivered nothing left the user with a
    hanging progress message (e.g. Yandex Music on prod, 19-20.11): it is
    recorded as ``no_result``.

    :param mark: The mark of the request.
    :return: Reason to record or None.
    """
    if mark.reason is not None:
        return mark.reason
    if not mark.delivered:
        return "no_result"
    return None


def host_of(url: str) -> str:
    """Host of a link without www, empty for an invalid one."""
    if "://" not in url:
        url = "https://" + url
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


class _LastErrorHandler(logging.Handler):
    """Keep the last ERROR line logged while a tracked request is handled."""

    def emit(self, record: logging.LogRecord) -> None:
        current = _CURRENT.get()
        if current is None:
            return
        try:
            text = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                text = f"{text} | {type(record.exc_info[1]).__name__}: "
                text += str(record.exc_info[1])
        except Exception:
            return
        current.error = text[:MAX_ERROR_LENGTH]


_HANDLER = _LastErrorHandler(level=logging.ERROR)


def _install_handler() -> None:
    root = logging.getLogger()
    if _HANDLER not in root.handlers:
        root.addHandler(_HANDLER)
