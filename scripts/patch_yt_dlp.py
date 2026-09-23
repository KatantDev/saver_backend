import logging
import os

import yt_dlp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def replace_in_files(replacements: list[dict[str, str]]) -> None:
    for item in replacements:
        file_path = item.get("path")
        search_text = item.get("search")
        replace_text = item.get("replace")

        if not file_path or search_text is None or replace_text is None:
            logging.warning("Skipping: invalid parameters for %s", item)
            continue

        try:
            if not os.path.exists(file_path):
                logging.error("File not found: %s", file_path)
                continue

            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            new_content = content.replace(search_text, replace_text)

            if new_content != content:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                logging.info("Replacement completed in: %s", file_path)
            else:
                logging.info("Nothing found in: %s", file_path)

        except Exception as e:
            logging.error("Error processing %s: %s", file_path, e)


YDL_EXTRACTOR_DIR = os.path.join(os.path.dirname(yt_dlp.__file__), "extractor")

replacements = [
    {
        "path": os.path.join(YDL_EXTRACTOR_DIR, "odnoklassniki.py"),
        "search": "metadata = self._parse_json(metadata, video_id)",
        "replace": (
            "if isinstance(metadata, str):\n"
            "                metadata=self._parse_json(metadata,video_id)"
        ),
    },
]

replace_in_files(replacements)
