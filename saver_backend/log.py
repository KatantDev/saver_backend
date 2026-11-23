import logging
import socket
import sys
import time
from pathlib import Path
from typing import Union

from loguru import logger

from saver_backend.settings import settings


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
        sys.stdout,
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
            settings.log_dir / (process_name() + ".{time:YYYY-MM-DD}.log"),
            level=settings.log_level.value,
            rotation="00:00",
        )
    except OSError as error:
        logger.warning(
            "File log is off, {} is not writable: {}", settings.log_dir, error
        )
