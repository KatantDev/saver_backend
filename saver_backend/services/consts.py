from pathlib import Path

BASE_DOWNLOAD_PATH = Path("downloads")
# A bot can upload a file of at most 2000 MB even through its own Bot API server;
# a bigger one is refused with FILE_PARTS_INVALID after the whole download (93
# videos on prod on 21-23.11 while the limit here was 3 GB). The margin covers
# the container overhead and sizes that are only estimates.
MAX_FILE_SIZE_BYTES = 1950 * 1024 * 1024
# The limit itself, for a file that is already downloaded.
TELEGRAM_UPLOAD_LIMIT_BYTES = 2000 * 1024 * 1024
