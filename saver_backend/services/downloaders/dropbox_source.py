import logging
import re
from typing import Any, ClassVar
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.downloaders.rclone_source import RcloneSourceController


class DropboxController(RcloneSourceController):
    """
    Controller for downloading files from Dropbox.

    Transforms public share links to direct download links (dl=1) and uses
    HEAD requests to resolve the actual filename.
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.DROPBOX

    def _get_direct_link(self, url: str) -> str:
        """
        Convert Dropbox share URL to direct download URL.

        Keeps all other parameters (like rlkey) intact.
        Changes dl=0 to dl=1.
        """
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query["dl"] = ["1"]

        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(query, doseq=True),
                parsed.fragment,
            ),
        )

    def _parse_content_disposition(self, header: str) -> str | None:
        """
        Robustly parse Content-Disposition header.

        Needed because regex groups in a single line can be fragile with optional quotes
        """
        if not header:
            return None

        # 1. Try filename*=UTF-8''... (Modern standard, handles Cyrillic)
        match_encoded = re.search(
            r"filename\*=UTF-8''([^;]+)",
            header,
            flags=re.IGNORECASE,
        )
        if match_encoded:
            return unquote(match_encoded.group(1))

        # 2. Try filename="..." (Quoted)
        match_quoted = re.search(r'filename="([^"]+)"', header, flags=re.IGNORECASE)
        if match_quoted:
            return match_quoted.group(1)

        # 3. Try filename=... (Simple, no quotes)
        match_simple = re.search(r"filename=([^;]+)", header, flags=re.IGNORECASE)
        if match_simple:
            return match_simple.group(1).strip()

        return None

    async def _get_filename(self, direct_url: str) -> str:
        """Resolve filename via HEAD request."""
        default_name = "dropbox_content"
        try:
            # We must use follow_redirects=True because dl=1 redirects to content server
            response = await self._client.head(direct_url)

            # 1. Try Header (Primary source of truth)
            content_disp = response.headers.get("content-disposition", "")
            filename = self._parse_content_disposition(content_disp)
            if filename:
                return filename

            # 2. Check content-type for ZIP (folders are downloaded as zip)
            # Sometimes Dropbox doesn't send Content-Disposition for folders on HEAD
            content_type = response.headers.get("content-type", "")

            # 3. Fallback to URL path
            path = urlparse(response.url.path).path
            path_name = path.split("/")[-1]

            if path_name and "zip_download_get" not in path_name:
                filename = unquote(path_name)
            else:
                # If path is generic, try original URL
                original_path = urlparse(self._resolution.url).path
                filename = unquote(original_path.split("/")[-1])

            if "zip" in content_type and filename and not filename.endswith(".zip"):
                filename += ".zip"

            return filename or default_name

        except Exception as e:
            logging.warning(f"Could not resolve filename from Dropbox: {e}")
            return default_name

    async def _perform_download(self) -> Any:
        direct_url = self._get_direct_link(self._resolution.url)
        filename = await self._get_filename(direct_url)

        safe_name = self._sanitize_filename(filename)
        logging.info("Downloading Dropbox file as: %s", safe_name)

        target_file = self._download_directory / safe_name

        cmd = [
            "rclone",
            "copyurl",
            direct_url,
            str(target_file),
        ]
        await self._execute_rclone(cmd)

        if not target_file.exists():
            raise FileNotFoundError("File download failed")

        return target_file
