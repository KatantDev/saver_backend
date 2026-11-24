import io
import logging
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from saver_backend import log
from saver_backend.services import failure_log
from saver_backend.services.error_journal import ErrorJournal
from saver_backend.services.log_text import fingerprint, sanitize

PROXY = "socks5://user1:secretpass@185.10.20.30:1080"
TOKEN = "7577004438:AAH5kq3mXl0aBcDeFgHiJkLmNoPqRsTuVwX"


def _record(message: str, error: BaseException | None = None) -> logging.LogRecord:
    exc_info: Any = None
    if error is not None:
        try:
            raise error
        except type(error) as caught:
            exc_info = (type(caught), caught, caught.__traceback__)
    return logging.LogRecord("yt", logging.ERROR, __file__, 1, message, None, exc_info)


def test_proxy_password_and_bot_token_are_not_stored() -> None:
    """yt-dlp quotes the proxy, Telegram errors quote the bot token."""
    text = sanitize(
        f"Unable to connect to proxy {PROXY}; "
        f"https://api.telegram.org/bot{TOKEN}/sendVideo failed",
    )

    assert "secretpass" not in text
    assert "user1" not in text
    assert TOKEN not in text
    assert "<credentials>" in text
    assert "<token>" in text


def test_same_error_on_other_videos_is_one_group() -> None:
    """The video id, the user id and the link do not split a group."""
    first = fingerprint(
        "DownloadError",
        "ERROR: [youtube] RpT8-vgzkS0: Sign in to confirm; user_id=79489201 "
        "https://youtu.be/RpT8-vgzkS0",
    )
    second = fingerprint(
        "DownloadError",
        "ERROR: [youtube] fO1LSTMksAk: Sign in to confirm; user_id=453938514 "
        "https://youtu.be/fO1LSTMksAk",
    )
    other = fingerprint("DownloadError", "ERROR: [vk] Video only for followers")

    assert first == second
    assert first != other


def test_repeated_error_is_counted_not_duplicated() -> None:
    """A burst of the same error becomes one row with a counter."""
    journal = ErrorJournal("worker")
    for video in ("RpT8-vgzkS0", "fO1LSTMksAk", "L2AgPB1eSWs"):
        journal.emit(
            _record(f"[youtube] {video}: Sign in to confirm", ValueError("boom")),
        )
    journal.emit(_record("FILE_PARTS_INVALID"))

    pending = journal.take_pending()

    assert sorted(item.count for item in pending) == [1, 3]
    grouped = next(item for item in pending if item.count == 3)
    assert grouped.error_type == "ValueError"
    assert grouped.traceback is not None
    assert "ValueError: boom" in grouped.traceback
    assert journal.take_pending() == []


def test_stack_trace_keeps_no_secrets() -> None:
    """The stack trace goes through the same cleaning as the message."""
    journal = ErrorJournal("worker")
    journal.emit(_record("download failed", OSError(f"proxy {PROXY} refused")))

    (item,) = journal.take_pending()

    assert item.traceback is not None
    assert "secretpass" not in item.traceback


@pytest.mark.anyio
async def test_errors_are_written_in_one_transaction() -> None:
    """Each group is one upsert; the batch is committed once."""
    journal = ErrorJournal("worker")
    journal.emit(_record("first error"))
    journal.emit(_record("second error", ValueError("x")))
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    written = await journal.flush_to(factory)

    assert written == 2
    assert session.execute.await_count == 2
    session.commit.assert_awaited_once()
    assert await journal.flush_to(factory) == 0


def test_failed_request_gets_the_stack_trace() -> None:
    """The failed request keeps where the error happened, not only its text."""
    mark = failure_log.start()
    try:
        raise ValueError(f"HTTP Error 403 through {PROXY}")
    except ValueError:
        logging.getLogger("x").exception("download failed")

    assert mark.traceback is not None
    assert "ValueError" in mark.traceback
    assert "test_failed_request_gets_the_stack_trace" in mark.traceback
    assert mark.error is not None
    assert "secretpass" not in mark.error
    assert "secretpass" not in mark.traceback


def test_old_log_files_are_removed(tmp_path: Path) -> None:
    """Files older than the keep period go, fresh ones stay."""
    old = tmp_path / "taskiq-worker.2026-11-10.log"
    fresh = tmp_path / "taskiq-worker.2026-11-23.log"
    old.write_text("old")
    fresh.write_text("fresh")
    week_ago = time.time() - 8 * 24 * 60 * 60
    os.utime(old, (week_ago, week_ago))

    log.remove_old_logs(tmp_path, keep_days=7)

    assert not old.exists()
    assert fresh.exists()


def test_unwritable_log_folder_does_not_stop_the_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder owned by another user only switches the file log off."""
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    monkeypatch.setattr(log.settings, "log_dir", blocker / "logs")

    log.add_file_log()


WEBHOOK_LINE = (
    '172.18.0.10:42928 - "POST /api/webhook/telegram/'
    + TOKEN.replace(":", "%3A")
    + ' HTTP/1.1" 204'
)


def test_token_in_the_webhook_address_is_removed() -> None:
    """Dev and prod 24.11: the access log kept the bot token in every line."""
    text = sanitize(WEBHOOK_LINE)

    assert TOKEN.split(":")[1] not in text
    assert "/api/webhook/telegram/<token> HTTP/1.1" in text


def test_log_stream_writes_lines_without_secrets() -> None:
    """Everything that goes to the container log passes the same filter."""
    stream = io.StringIO()
    safe = log.SafeStream(stream)

    safe.write(f"{WEBHOOK_LINE}\nproxy {PROXY} failed\n")
    safe.flush()

    written = stream.getvalue()
    assert TOKEN.split(":")[1] not in written
    assert "secretpass" not in written
    assert "POST /api/webhook/telegram/<token>" in written


def test_file_log_is_clean_and_named_by_day(tmp_path: Path) -> None:
    """The file log keeps its names and gets no secrets, a new day a new file."""
    sink_id = logger.add(log.DailyLogFile(tmp_path, "api"), format="{message}")
    try:
        logger.info(WEBHOOK_LINE)
    finally:
        logger.remove(sink_id)

    files = list(tmp_path.glob("api.*.log"))
    assert [path.name for path in files] == [f"api.{time.strftime('%Y-%m-%d')}.log"]
    text = files[0].read_text()
    assert TOKEN.split(":")[1] not in text
    assert "<token>" in text

    other_day = log.DailyLogFile(tmp_path, "api")
    other_day._open("2026-01-01")  # noqa: SLF001
    other_day.write("line of another day\n")
    other_day.stop()
    assert (tmp_path / "api.2026-01-01.log").read_text() == "line of another day\n"
