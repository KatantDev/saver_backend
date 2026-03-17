import logging
import time
from pathlib import Path
from typing import ClassVar

from saver_backend.services.consts import BASE_DOWNLOAD_PATH


class CleanupService:
    """
    Service for cleaning up old files from download directory.

    Scans the download directory and removes files older than specified age limit.
    """

    DEFAULT_AGE_LIMIT: ClassVar[int] = 3600  # 1 hour in seconds

    def __init__(
        self,
        download_path: Path = BASE_DOWNLOAD_PATH,
        age_limit: int = DEFAULT_AGE_LIMIT,
    ) -> None:
        """
        Initialize cleanup service.

        :param download_path: Path to downloads directory.
        :param age_limit: Age limit in seconds for files to be deleted.
        """

        self._download_path = download_path
        self._age_limit = age_limit

    async def cleanup(self) -> None:
        """Scan download directory and delete old files."""

        if not self._download_path.is_dir():
            logging.error(
                "Downloads directory not found at: %s",
                self._download_path,
            )
            return

        logging.info(
            "Starting cleanup of old files in %s (age limit: %d seconds)",
            self._download_path,
            self._age_limit,
        )

        now = time.time()
        files_deleted = 0
        files_scanned = 0

        for file_path in self._download_path.rglob("*"):
            if not file_path.is_file():
                continue

            files_scanned += 1
            if self._is_older_than_limit(file_path, now):
                self._delete_file(file_path)
                files_deleted += 1

        logging.info(
            "Cleanup finished. Scanned: %d files, Deleted: %d files.",
            files_scanned,
            files_deleted,
        )

    def _is_older_than_limit(self, file_path: Path, now: float) -> bool:
        """
        Check if file is older than age limit.

        :param file_path: Path to file.
        :param now: Current timestamp.
        :return: True if file is older than limit.
        """

        try:
            file_age = now - file_path.stat().st_mtime
            return file_age > self._age_limit
        except OSError as e:
            logging.error("Failed to get file stats for %s: %s", file_path, e)
            return False

    def _delete_file(self, file_path: Path) -> None:
        """
        Delete a single file.

        :param file_path: Path to file to delete.
        """

        try:
            file_age_minutes = (time.time() - file_path.stat().st_mtime) / 60
            file_path.unlink()
            logging.info(
                "Deleted old file: %s (age: %.2f minutes)",
                file_path,
                file_age_minutes,
            )
        except OSError as e:
            logging.error("Failed to delete file %s: %s", file_path, e)
