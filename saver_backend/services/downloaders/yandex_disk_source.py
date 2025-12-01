import asyncio
import logging
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.rclone_source import RcloneSourceController


class YandexDiskController(RcloneSourceController):
    """
    Controller for downloading files and folders from Yandex Disk.

    Uses Yandex Disk Public API to retrieve information.
    - Files are downloaded via `rclone copyurl`.
    - Folders are handled by iterating over items and downloading them individually.
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.YANDEX_DISK
    API_URL = "https://cloud-api.yandex.net/v1/disk/public/resources"

    MAX_CONCURRENT_DOWNLOADS = 16

    async def _get_resource_info(self, url: str) -> dict[str, Any]:
        """
        Fetch resource metadata from Yandex API.

        :param url: Public Yandex Disk URL.
        :return: JSON response dictionary.
        """
        encoded_url = quote(url)
        api_req_url = f"{self.API_URL}?public_key={encoded_url}&limit=500"

        response = await self._client.get(api_req_url)
        response.raise_for_status()
        return response.json()

    async def _download_file(
        self,
        download_url: str,
        filename: str,
        output_dir: Path,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        """
        Download a single file to a specific directory using rclone copyurl.

        Uses semaphore to limit concurrency if provided.
        """
        if semaphore:
            async with semaphore:
                await self._execute_copyurl(download_url, filename, output_dir)
        else:
            await self._execute_copyurl(download_url, filename, output_dir)

    async def _execute_copyurl(
        self,
        download_url: str,
        filename: str,
        output_dir: Path,
    ) -> None:
        """Internal helper to execute the rclone command."""
        safe_name = self._sanitize_filename(filename)
        target_path = output_dir / safe_name

        cmd = [
            "rclone",
            "copyurl",
            download_url,
            str(target_path),
            "--no-check-certificate",
        ]
        await self._execute_rclone(cmd)

    async def _process_folder(
        self,
        data: dict[str, Any],
        root_name: str,
    ) -> Path:
        """Handle downloading of a folder."""
        target_dir = self._download_directory / root_name
        target_dir.mkdir(parents=True, exist_ok=True)

        embedded = data.get("_embedded", {})
        items = embedded.get("items", [])
        if not items:
            raise ValueError("Folder is empty or contains no public files")

        logging.info(
            "Downloading folder '%s' containing %s items",
            root_name,
            len(items),
        )

        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_DOWNLOADS)
        tasks = []

        for item in items:
            if item.get("type") == "file":
                file_url = item.get("file")
                file_name = item.get("name")
                if file_url and file_name:
                    tasks.append(
                        self._download_file(
                            file_url,
                            file_name,
                            target_dir,
                            semaphore,
                        ),
                    )

        if tasks:
            await asyncio.gather(*tasks)

        if not any(target_dir.iterdir()):
            raise FileNotFoundError("No files were downloaded from the folder")

        return target_dir

    async def _process_file(self, data: dict[str, Any], safe_root_name: str) -> Path:
        """Handle downloading of a single file."""
        download_url = data.get("file")
        if not download_url:
            raise ValueError("Could not retrieve download URL for file")

        await self._download_file(
            download_url,
            safe_root_name,
            self._download_directory,
        )

        target_file = self._download_directory / safe_root_name
        if not target_file.exists():
            raise FileNotFoundError("File download failed")

        return target_file

    async def _perform_download(self) -> Any:
        data = await self._get_resource_info(self._resolution.url)

        resource_type = data.get("type")
        safe_root_name = self._sanitize_filename(data.get("name", "yandex_download"))

        if resource_type == "dir":
            return await self._process_folder(data, safe_root_name)
        if resource_type == "file":
            return await self._process_file(data, safe_root_name)
        raise ValueError(f"Unsupported resource type: {resource_type}")
