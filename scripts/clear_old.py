import logging
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] - %(message)s",
)

BASE_DOWNLOAD_PATH = Path(__file__).resolve().parent.parent / "downloads"
AGE_LIMIT_SECONDS = 30 * 60


def clear_old_files() -> None:
    """
    Scans all subdirectories of BASE_DOWNLOAD_PATH and deletes files.

    older than AGE_LIMIT_SECONDS.
    """
    if not BASE_DOWNLOAD_PATH.is_dir():
        logging.error(
            "Downloads directory not found at: %s",
            BASE_DOWNLOAD_PATH,
        )
        return

    logging.info("Starting cleanup of old files in %s", BASE_DOWNLOAD_PATH)
    now = time.time()
    files_deleted = 0
    total_files_scanned = 0

    for file_path in BASE_DOWNLOAD_PATH.rglob("*"):
        try:
            if file_path.is_file():
                total_files_scanned += 1
                file_mod_time = file_path.stat().st_mtime
                file_age = now - file_mod_time

                if file_age > AGE_LIMIT_SECONDS:
                    logging.info(
                        "Deleting old file: %s (age: %.2f minutes)",
                        file_path,
                        file_age / 60,
                    )
                    file_path.unlink()
                    files_deleted += 1
        except Exception as e:
            logging.error("Failed to process file %s: %s", file_path, e)

    logging.info(
        "Cleanup finished. Scanned: %d files, Deleted: %d files.",
        total_files_scanned,
        files_deleted,
    )


if __name__ == "__main__":
    clear_old_files()
