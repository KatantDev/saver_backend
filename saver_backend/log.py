import logging
import socket
import sys
import time
from pathlib import Path
from typing import IO, Any, Union

from loguru import logger

from saver_backend.services.log_text import sanitize
from saver_backend.settings import settings


class SafeStream:
    """
    Log sink that removes secrets from every line before it is written.

    The access log quoted the webhook address with the bot token in it, and
    stack traces quote proxies with their passwords: all of that went to the
    container log and to the log files (seen on dev and prod on 24.11).
    """

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream

    def write(self, message: Any) -> None:
        """
        Write one formatted log line, stack trace included.

        :param message: The line as loguru formatted it.
        """
        self._stream.write(sanitize(str(message)))

    def flush(self) -> None:
        """Flush the stream."""
        self._stream.flush()


class DailyLogFile:
    """
    Log sink for the file log: one file per day, secrets removed.

    Keeps the names loguru gave the files before, ``<process>.<date>.log``.
    """

    def __init__(self, log_dir: Path, name: str) -> None:
        self._dir = log_dir
        self._name = name
        self._day = ""
        self._file: IO[str] | None = None
        # Opened right away: a folder that cannot be written fails here
        self._open(time.strftime("%Y-%m-%d"))

    def _open(self, day: str) -> None:
        if self._file is not None:
            self._file.close()
        path = self._dir / f"{self._name}.{day}.log"
        self._file = path.open("a", encoding="utf-8")
        self._day = day

    def write(self, message: Any) -> None:
        """
        Write one formatted log line to the file of its day.

        :param message: The line as loguru formatted it.
        """
        record = getattr(message, "record", None)
        day = record["time"].strftime("%Y-%m-%d") if record else self._day
        if day != self._day:
            self._open(day)
        if self._file is not None:
            self._file.write(sanitize(str(message)))

    def flush(self) -> None:
        """Flush the file."""
        if self._file is not None:
            self._file.flush()

    def stop(self) -> None:
        """Close the file when the sink is removed."""
        if self._file is not None:
            self._file.close()
            self._file = None


class InterceptHandler(logging.Handler):
    """
    Default handler from examples in loguru documentation.

    This handler intercepts all log requests and
    passes them to loguru.

    For more info see:
    https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    """

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        """
        Propagates logs to loguru.

        :param record: record to log.
        """
        try:
            level: Union[str, int] = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level,
            record.getMessage(),
        )


def configure_logging() -> None:  # pragma: no cover
    """Configures logging."""
    intercept_handler = InterceptHandler()

    logging.basicConfig(handlers=[intercept_handler], level=logging.NOTSET)

    for logger_name in logging.root.manager.loggerDict:
        if logger_name.startswith("uvicorn."):
            logging.getLogger(logger_name).handlers = []
        if logger_name.startswith("taskiq."):
            logging.getLogger(logger_name).root.handlers = [intercept_handler]

    # change handler for default uvicorn logger
    logging.getLogger("uvicorn").handlers = [intercept_handler]
    logging.getLogger("uvicorn.access").handlers = [intercept_handler]

    # set logs output, level and format
    logger.remove()
    logger.add(
        SafeStream(sys.stdout),
        level=settings.log_level.value,
    )
    add_file_log()


def process_name() -> str:
    """Name of this process for the log file, e.g. api or taskiq-worker."""
    name = socket.gethostname().removeprefix("saver_backend-")
    return "".join(char if char.isalnum() or char == "-" else "_" for char in name)


def remove_old_logs(log_dir: Path, keep_days: int) -> None:
    """Delete log files older than keep_days; other processes may do the same."""
    limit = time.time() - keep_days * 24 * 60 * 60
    for path in log_dir.glob("*.log"):
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
        except OSError:
            continue


def add_file_log() -> None:
    """
    Also write the log to a file that survives deploys.

    The container log is lost every time a deploy replaces the container, so the
    reason of a failure seen an hour later could not be found. The file name
    carries the date, so processes sharing the folder never rename a file under
    each other. A folder that cannot be written only skips the file log.
    """
    if settings.log_dir is None:
        return
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        remove_old_logs(settings.log_dir, settings.log_keep_days)
        logger.add(
            DailyLogFile(settings.log_dir, process_name()),
            level=settings.log_level.value,
        )
    except OSError as error:
        logger.warning(
            "File log is off, {} is not writable: {}", settings.log_dir, error
        )
