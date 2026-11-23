"""Turn log records into text that is safe to store: no secrets, bounded size."""

import hashlib
import logging
import re
import traceback

MAX_TRACEBACK_LENGTH = 8000
FINGERPRINT_TEXT_LENGTH = 200

# scheme://user:password@host — a proxy or a database address
CREDENTIALS_RE = re.compile(
    r"[a-z0-9+]+://[^\s/@:]+:[^\s/@]+@[^\s/'\")]+", re.IGNORECASE
)
# 123456789:AA... — a Telegram bot token, also inside api.telegram.org links
BOT_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")
URL_RE = re.compile(r"https?://\S+")
ID_RE = re.compile(r"\b(?=\w*\d)[\w-]{8,}\b")
NUMBER_RE = re.compile(r"\d+")


def sanitize(text: str) -> str:
    """
    Remove secrets from a text before it is stored or shown.

    yt-dlp and aiohttp errors quote the proxy with its login and password, and
    Telegram request errors quote the bot token.

    :param text: Log message or stack trace.
    :return: The text with credentials and tokens replaced.
    """
    text = CREDENTIALS_RE.sub("<credentials>", text)
    return BOT_TOKEN_RE.sub("<token>", text)


def fingerprint(error_type: str, message: str) -> str:
    """
    Group key of an error: its type and its text without the changing parts.

    :param error_type: Exception class name, empty for a plain error line.
    :param message: Sanitized log message.
    :return: 40 hex characters.
    """
    text = URL_RE.sub("<url>", message)
    text = ID_RE.sub("<id>", text)
    text = NUMBER_RE.sub("N", text)[:FINGERPRINT_TEXT_LENGTH]
    return hashlib.sha1(f"{error_type}|{text}".encode()).hexdigest()  # noqa: S324


def format_traceback(record: logging.LogRecord) -> str | None:
    """
    Stack trace of a log record, sanitized and cut to the last frames.

    :param record: Log record.
    :return: The stack trace, None when the record has no exception.
    """
    if not record.exc_info or record.exc_info[1] is None:
        return None
    text = sanitize("".join(traceback.format_exception(*record.exc_info)))
    return text[-MAX_TRACEBACK_LENGTH:]
