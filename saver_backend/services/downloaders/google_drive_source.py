import json
import logging
import re
from pathlib import Path
from typing import Any, ClassVar

import httpx

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.rclone_source import RcloneSourceController


class GoogleDriveController(RcloneSourceController):
    """
    Controller for downloading files from Google Drive.

    Uses rclone with OAuth token from config to download files and folders.
    Fetches real filenames via Google Drive API before downloading.
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.GOOGLE_DRIVE
    RCLONE_REMOTE_NAME = "gdrive"
    API_URL: str = "https://www.googleapis.com/drive/v3/files"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._resource_id: str | None = None
        self._is_folder: bool = False

    def _extract_info(self) -> None:
        """Extract ID and type from resolution metadata populated by Detector."""
        metadata = self._resolution.metadata
        self._resource_id = metadata.get("id")
        self._is_folder = metadata.get("type") == "folder"

        if not self._resource_id:
            raise ValueError("Could not extract ID from Google Drive URL")

    async def _fetch_filename_from_api(self, file_id: str, token: str) -> str:
        """Fetch real filename/foldername from Google Drive API v3."""
        url = f"{self.API_URL}/{file_id}?fields=name"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("name", "download_content")
                logging.warning("API Error getting filename: %s", response.text)
        except Exception as e:
            logging.warning("Error fetching filename: %s", str(e))

        return "download_content"

    async def _refresh_and_read_token(self) -> str:
        """
        1. Run 'rclone about' to force token refresh and write to config file.

        2. Read the config file to get the fresh access token.
        """
        cmd = ["rclone", "about", f"{self.RCLONE_REMOTE_NAME}:"]
        await self._execute_rclone(cmd)

        try:
            config_path = Path("/root/.config/rclone/rclone.conf")
            if not config_path.exists():
                raise FileNotFoundError("rclone.conf not found inside container")

            content = config_path.read_text()
            match = re.search(r"token\s*=\s*({.*?})", content)
            if match:
                token_data = json.loads(match.group(1))
                return token_data.get("access_token")
        except Exception as e:
            logging.error(f"Failed to parse token from config: {e}")

        raise RuntimeError("Could not retrieve Access Token from config file")

    async def _perform_download(self) -> Path:
        self._extract_info()
        if self._resource_id is None:
            raise ValueError("Resource ID is not set")

        token = await self._refresh_and_read_token()

        real_name = await self._fetch_filename_from_api(self._resource_id, token)
        safe_name = self._sanitize_filename(real_name)
        if not safe_name:
            safe_name = "download_content"

        logging.info(
            "Processing %s Name='%s' IsFolder=%s",
            self._resource_id,
            safe_name,
            self._is_folder,
        )

        if self._is_folder:
            target_dir = self._download_directory / safe_name
            source = f"{self.RCLONE_REMOTE_NAME},root_folder_id={self._resource_id}:"
            cmd = [
                "rclone",
                "copy",
                source,
                str(target_dir),
                "--drive-acknowledge-abuse",
                "--transfers",
                "8",
                "--max-size=2G",
            ]

            await self._execute_rclone(cmd)
            if not any(target_dir.iterdir()):
                raise FileNotFoundError("Empty folder")

            return target_dir

        target_file = self._download_directory / safe_name
        download_url = f"{self.API_URL}/{self._resource_id}?alt=media"
        cmd = [
            "rclone",
            "copyurl",
            download_url,
            str(target_file),
            f"--header=Authorization: Bearer {token}",
            "--drive-acknowledge-abuse",
        ]

        await self._execute_rclone(cmd)
        if not target_file.exists() or target_file.stat().st_size == 0:
            raise FileNotFoundError("File download failed via copyurl")

        return target_file
