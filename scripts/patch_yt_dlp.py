import os
from typing import List, Dict
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def replace_in_files(replacements: List[Dict[str, str]]) -> None:
    """
    Performs search and replace of text in specified files.

    Args:
        replacements: List of dictionaries with parameters:
            - 'path': path to the file
            - 'search': text to search for
            - 'replace': text to replace with
    """
    for item in replacements:
        file_path = item.get("path")
        search_text = item.get("search")
        replace_text = item.get("replace")

        if not all([file_path, search_text is not None, replace_text is not None]):
            logging.warning(f"Skipping: invalid parameters for {item}")
            continue

        try:
            # Check if file exists
            if not os.path.exists(file_path):
                logging.warning(f"dir: {os.getcwd()} \n {os.listdir()}")
                logging.error(f"File not found: {file_path}")
                continue

            # Read file content
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()

            # Perform replacement
            new_content = content.replace(search_text, replace_text)

            # If content changed, write it back
            if new_content != content:
                with open(file_path, "w", encoding="utf-8") as file:
                    file.write(new_content)
                logging.info(f"Replacement completed in: {file_path}")
            else:
                logging.info(f"Nothing found in: {file_path}")

        except Exception as e:
            logging.error(f"Error processing {file_path}: {e}")


# Example usage
replacements = [
    {
        "path": "/usr/local/lib/python3.13/site-packages/yt_dlp/extractor/yandexmusic.py",
        "search": "download_data['src'], track_id,",
        "replace": "f'https:{download_data[\"src\"]}' if download_data['src'].startswith('//')  else download_data['src'], track_id,",
    },
    {
        "path": "/usr/local/lib/python3.13/site-packages/yt_dlp/extractor/yandexmusic.py",
        "search": "f_url = 'http://",
        "replace": "f_url = 'https://",
    },
    {
        "path": "/usr/local/lib/python3.13/site-packages/yt_dlp/extractor/yandexmusic.py",
        "search": "thumbnail = 'http://",
        "replace": "thumbnail = 'https://",
    },
]

replace_in_files(replacements)
