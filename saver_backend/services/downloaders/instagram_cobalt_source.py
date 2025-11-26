import logging
import uuid
from pathlib import Path
from typing import Any, ClassVar

from httpx import AsyncClient, RequestError

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import (
    CobaltResponse,
    PhotoDTO,
    VideoDTO,
)
from saver_backend.settings import settings


class InstagramCobaltController(BaseSourceController):
    """
    Controller for downloading Instagram content via self-hosted Cobalt instance.

    This controller handles both single posts (video/photo), carousels (albums),
    and stories by delegating the scraping logic to the Cobalt service.
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.INSTAGRAM_COBALT
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.LOCAL
    DIRECT_URL_DOWNLOAD: ClassVar[bool] = True

    VALID_STATUSES: ClassVar[tuple[str, ...]] = (
        "stream",
        "redirect",
        "picker",
        "tunnel",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Initialize the controller and the HTTP client.

        :param args: Positional arguments for BaseSourceController.
        :param kwargs: Keyword arguments for BaseSourceController.
        """
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)

        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._client = AsyncClient()

    async def close(self) -> None:
        """Close the HTTP client resources."""
        await self._client.aclose()

    async def _fetch_cobalt_data(self, url: str) -> CobaltResponse | None:
        """
        Send a request to the Cobalt API to get media information.

        :param url: The Instagram URL to process.
        :return: A CobaltResponse object or None if the request failed.
        """
        payload = {
            "url": url,
        }

        api_endpoint = settings.cobalt_api_url.rstrip("/")

        try:
            response = await self._client.post(
                api_endpoint,
                json=payload,
                headers=self._headers,
                timeout=60,
            )
            if response.status_code != 200:
                try:
                    data = response.json()
                    if "status" in data and data["status"] == "error":
                        return CobaltResponse.model_validate(data)
                    logging.error(
                        "Cobalt HTTP Error %s: %s",
                        response.status_code,
                        response.text,
                    )
                except Exception:
                    logging.error("Cobalt non-JSON Error %s", response.status_code)

                return None

            response.raise_for_status()
            data = response.json()
            return CobaltResponse.model_validate(data)
        except (RequestError, ValueError) as e:
            logging.error("Cobalt API connection error for %s: %s", url, e)
            return None
        except Exception as e:
            logging.exception("Unexpected error in Cobalt controller: %s", e)
            return None

    async def download_video(self) -> None:
        """
        Main execution method to download/process the video or photo.

        1. Checks cache using the shortcode/id.
        2. Fetches data from Cobalt.
        3. Parses the response.
        4. Sends the result.
        """
        source_id = str(self._resolution.metadata.get("code", "unknown"))
        if await self.send_video_from_cache(source_id=source_id, quality="best"):
            return

        self._process_percent(16)
        cobalt_resp = await self._fetch_cobalt_data(self._resolution.url)
        if not cobalt_resp:
            await self._send_error_message()
            return
        if cobalt_resp.status == "error":
            error_code = "unknown"
            if cobalt_resp.error:
                error_code = str(cobalt_resp.error.code)
            logging.warning("Cobalt returned error: %s", error_code)
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return

        if cobalt_resp.status not in self.VALID_STATUSES:
            logging.error("Unknown Cobalt status: %s", cobalt_resp.status)
            await self._send_error_message()
            return

        self._process_percent(52)
        dtos = self._extract_dtos(cobalt_resp, source_id)

        self._process_percent(86)
        await self._send_media(dtos)

        self.cleanup_files(dtos)

    def _create_dto(
        self,
        original_url: str,
        url: str,
        source_id: str,
        media_type: str | None = None,
        filename: str | None = None,
        thumbnail_url: str | None = None,
    ) -> VideoDTO | PhotoDTO:
        """
        Helper factory to create DTOs from Cobalt items.

        Determines if the content is a video or photo based on explicit type
        or filename extension.
        """
        is_video = False
        if media_type == "video":
            is_video = True
        elif media_type == "photo":
            is_video = False
        elif filename:
            lower_name = filename.lower()
            if not any(
                lower_name.endswith(ext) for ext in (".jpg", ".png", ".webp", ".jpeg")
            ):
                is_video = True

        if is_video:
            return VideoDTO.from_cobalt(
                original_url=original_url,
                source_id=source_id,
                direct_url=url,
                thumbnail_url=thumbnail_url,
            )
        return PhotoDTO.from_cobalt(
            url=original_url,
            source_id=source_id,
            media_url=url,
        )

    def _extract_dtos(
        self,
        cobalt_resp: CobaltResponse,
        source_id: str,
    ) -> list[VideoDTO | PhotoDTO]:
        """
        Parse Cobalt response and extract DTOs.

        Handles 'stream', 'redirect', 'tunnel' (single files) and 'picker' (galleries).
        """
        dtos: list[VideoDTO | PhotoDTO] = []
        original_url = self._resolution.url

        # Case 1: Single File
        if cobalt_resp.status in ("stream", "redirect", "tunnel") and cobalt_resp.url:
            dtos.append(
                self._create_dto(
                    original_url=original_url,
                    url=cobalt_resp.url,
                    source_id=source_id,
                    filename=cobalt_resp.filename,
                ),
            )

        # Case 2: Gallery (Picker)
        elif cobalt_resp.status == "picker" and cobalt_resp.picker:
            for item in cobalt_resp.picker:
                dtos.append(
                    self._create_dto(
                        original_url=original_url,
                        url=item.url,
                        source_id=source_id,
                        media_type=item.type,
                        thumbnail_url=item.thumb,
                    ),
                )

        return dtos

    async def _send_media(self, dtos: list[VideoDTO | PhotoDTO]) -> None:
        """
        Send the extracted DTOs to the user.

        :param dtos: List of content DTOs.
        """
        if not dtos:
            await self._send_error_message()
            return

        for dto in dtos:
            await self._download_content(dto)

        if len(dtos) == 1:
            item = dtos[0]
            if isinstance(item, VideoDTO):
                await self._send_video(item)
            elif isinstance(item, PhotoDTO):
                await self._telegram_bot_controller.send_finish_downloading_photo(
                    photo=item,
                    telegram_id=self._telegram_id,
                    message_id=self._message_id,
                )
                await self._create_history_entry()
        else:
            await self._telegram_bot_controller.send_finish_downloading_group(
                files=dtos,
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )
            await self._create_history_entry()

    async def _download_content(self, dto: VideoDTO | PhotoDTO) -> None:
        """
        Download ANY content from Cobalt to local disk.

        Includes:
        1. Main media file (video/photo).
        2. Thumbnail (for videos), if available.
        """

        unique_id = uuid.uuid4().hex[:8]

        target_url: str | None = None
        if isinstance(dto, PhotoDTO) and dto.media_url:
            target_url = dto.media_url
        elif isinstance(dto, VideoDTO) and dto.direct_download_url:
            target_url = dto.direct_download_url

        if target_url:
            ext = ".jpg" if isinstance(dto, PhotoDTO) else ".mp4"
            filename = f"{dto.source_id}_{unique_id}{ext}"

            local_path = await self._download_file_locally(target_url, filename)
            if local_path:
                dto.path = Path(local_path)
                if isinstance(dto, PhotoDTO):
                    dto.media_url = None
                elif isinstance(dto, VideoDTO):
                    dto.direct_download_url = None

        if isinstance(dto, VideoDTO) and dto.thumbnail_url:
            thumb_filename = f"{dto.source_id}_{unique_id}.jpg"

            local_thumb_path = await self._download_file_locally(
                dto.thumbnail_url,
                thumb_filename,
            )
            if local_thumb_path:
                dto.thumbnail = Path(local_thumb_path)
                dto.thumbnail_url = None

    async def _download_file_locally(self, url: str, filename: str) -> str | None:
        """
        Download file from Cobalt tunnel to local disk.

        Telegram cannot access internal docker URLs
        like http://saver_backend-cobalt:9000/...
        """
        try:
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()
                file_path = self._download_directory / filename
                with Path.open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
                return str(file_path)
        except Exception as e:
            logging.error("Failed to download file locally: %s", e)
            return None
