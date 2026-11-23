"""
Keep every logged error in the database, grouped and counted.

The Sentry server the project reports to has stored almost no errors for weeks
(23.11.2026: accepted on 2 days of 14, for every project on it), and the worker
log is lost on each deploy. This journal is the bot's own record: an ERROR line
becomes a row in ``error_event`` with its stack trace; the same error on the same
day only raises the row's counter.

Records are collected in memory by a logging handler (logging is synchronous and
may run in a thread) and written by a background task every few seconds.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from saver_backend.db.models.error_event_model import ErrorEventModel
from saver_backend.services.log_text import fingerprint, format_traceback, sanitize

FLUSH_INTERVAL_SECONDS = 15
MAX_PENDING = 500
MAX_MESSAGE_LENGTH = 2000


@dataclass
class PendingError:
    """One group of errors waiting to be written."""

    fingerprint: str
    day: date
    logger: str
    error_type: str
    message: str
    traceback: str | None
    count: int
    last_seen: datetime


class ErrorJournal(logging.Handler):
    """Logging handler that collects ERROR records for the database."""

    def __init__(self, process: str) -> None:
        super().__init__(level=logging.ERROR)
        self.process = process
        self._pending: dict[tuple[str, date], PendingError] = {}
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        """Remember the record; never raises."""
        try:
            message = sanitize(record.getMessage())[:MAX_MESSAGE_LENGTH]
            exc = record.exc_info[1] if record.exc_info else None
            error_type = type(exc).__name__ if exc is not None else ""
            now = datetime.now(UTC)
            key = (fingerprint(error_type, message), now.date())
            with self._lock:
                pending = self._pending.get(key)
                if pending is not None:
                    pending.count += 1
                    pending.last_seen = now
                    return
                if len(self._pending) >= MAX_PENDING:
                    return
                self._pending[key] = PendingError(
                    fingerprint=key[0],
                    day=key[1],
                    logger=record.name[:255],
                    error_type=error_type[:255],
                    message=message,
                    traceback=format_traceback(record),
                    count=1,
                    last_seen=now,
                )
        except Exception:  # noqa: S110
            pass

    def take_pending(self) -> list[PendingError]:
        """Hand over the collected errors and start collecting again."""
        with self._lock:
            pending = list(self._pending.values())
            self._pending = {}
        return pending

    async def flush_to(self, session_factory: async_sessionmaker[AsyncSession]) -> int:
        """
        Write the collected errors; the same error of the same day is counted.

        :param session_factory: Session factory of the process.
        :return: Number of groups written.
        """
        pending = self.take_pending()
        if not pending:
            return 0
        async with session_factory() as session:
            for item in pending:
                statement = insert(ErrorEventModel).values(
                    fingerprint=item.fingerprint,
                    day=item.day,
                    process=self.process,
                    logger=item.logger,
                    error_type=item.error_type,
                    message=item.message,
                    traceback=item.traceback,
                    count=item.count,
                    last_seen=item.last_seen,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=["fingerprint", "day"],
                    set_={
                        "count": ErrorEventModel.count + item.count,
                        "last_seen": item.last_seen,
                    },
                )
                await session.execute(statement)
            await session.commit()
        return len(pending)


async def _flush_forever(
    journal: ErrorJournal,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
        try:
            await journal.flush_to(session_factory)
        except Exception as error:
            # A warning, not an error: an error would feed the journal itself
            logging.warning("Could not write the error journal: %s", error)


def start_error_journal(
    process: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[ErrorJournal, asyncio.Task[None]]:
    """
    Start collecting errors of this process.

    :param process: Which process it is: api or worker.
    :param session_factory: Session factory of the process.
    :return: The journal and its background task, both needed to stop it.
    """
    journal = ErrorJournal(process)
    logging.getLogger().addHandler(journal)
    task = asyncio.create_task(_flush_forever(journal, session_factory))
    return journal, task


async def stop_error_journal(
    journal: ErrorJournal,
    task: asyncio.Task[None],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Write what is left and stop the journal."""
    task.cancel()
    logging.getLogger().removeHandler(journal)
    try:
        await journal.flush_to(session_factory)
    except Exception as error:
        logging.warning("Could not write the error journal on shutdown: %s", error)
