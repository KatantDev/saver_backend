import asyncio
import logging
import re
import shutil
import uuid
from abc import abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import httpx

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import DocumentDTO


class RcloneSourceController(BaseSourceController):
    """Base controller for downloading files via rclone."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.UNSUPPORTED
    RCLONE_REMOTE_NAME: str = "local"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        session_id = str(uuid.uuid4())
        self._download_directory = BASE_DOWNLOAD_PATH / "rclone_temp" / session_id
        self._download_directory.mkdir(parents=True, exist_ok=True)
        self._downloaded_file: Path | None = None

        self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    @abstractmethod
    async def _perform_download(self) -> Path:
        """
        Execute specific download logic.

        Must return path to the downloaded file or directory.
        """
        raise NotImplementedError

    def _sanitize_filename(self, filename: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

    async def _execute_rclone(self, cmd: list[str]) -> str:
        """Helper to run rclone process."""
        logging.info(f"Executing rclone: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            logging.error("Rclone failed: %s", error_msg)
            raise RuntimeError("Rclone error: %s", error_msg)
        return stdout.decode()

    async def _archive_directory(self, dir_path: Path, archive_name: str) -> Path:
        base_name = str(dir_path.parent / archive_name)
        logging.info("Archiving %s to %s.zip", dir_path, base_name)
        archive_path = await asyncio.to_thread(
            shutil.make_archive,
            base_name=base_name,
            format="zip",
            root_dir=str(dir_path),
        )
        return Path(archive_path)

    async def download_video(self) -> None:
        """Main execution flow."""
        try:
            self._process_percent(16)

            downloaded_path = await self._perform_download()

            self._process_percent(86)

            final_path: Path
            final_name: str

            if downloaded_path.is_dir():
                archive_name = downloaded_path.name
                final_path = await self._archive_directory(
                    downloaded_path,
                    archive_name,
                )
                final_name = final_path.name
                shutil.rmtree(downloaded_path)
            else:
                final_path = downloaded_path
                final_name = final_path.name

            self._downloaded_file = final_path

            document_dto = DocumentDTO(
                path=final_path,
                filename=final_name,
                url=self._resolution.url,
                title=final_path.stem,
            )
            await self._telegram_bot_controller.send_finish_downloading_document(
                document=document_dto,
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )

            await self._create_history_entry()
        except Exception:
            logging.exception("Rclone download failed")
            await self._send_error_message()
        finally:
            await self.close()

    async def close(self) -> None:
        """Clean up temporary files and directories."""
        await self._client.aclose()

        if self._download_directory.exists():
            shutil.rmtree(self._download_directory, ignore_errors=True)
            logging.info("Cleaned up rclone temp dir: %s", self._download_directory)

        if (
            self._downloaded_file
            and self._downloaded_file.exists()
            and self._downloaded_file.parent != self._download_directory
        ):
            self._downloaded_file.unlink(missing_ok=True)
