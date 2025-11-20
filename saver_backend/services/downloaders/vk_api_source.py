import asyncio
import logging
from typing import Any, ClassVar

from httpx import AsyncClient
from yt_dlp import DownloadError

from saver_backend.entities.enums import ContentTypeEnum, SourceEnum
from saver_backend.services.downloaders.schema import AudioDTO, PhotoDTO, VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController
from saver_backend.settings import settings


class VKAPIController(YtDlpController):
    """
    Unified controller for VK Wall posts and Photos via VK API.

    Handles:
    - vk.com/wall-XXXX_YYYY (Posts with mixed content)
    - vk.com/photo-XXXX_YYYY (Direct photos)
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.VK_API_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._api_client = AsyncClient(timeout=10.0)
        self._base_api_url = "https://api.vk.ru/method/"

        vk_params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
            "quiet": True,
            "noprogress": True,
        }
        self._yt_dlp.params.update(vk_params)

    async def close(self) -> None:
        """Close the HTTP client and the superclass resources."""
        await self._api_client.aclose()
        await super().close()

    async def _make_api_request(
        self,
        method: str,
        params: dict[str, Any],
    ) -> Any | None:
        """
        Execute VK API request with error handling.

        :param method: API method name (e.g. 'wall.getById').
        :param params: Method parameters (excluding access_token and v).
        :return: The 'response' part of the JSON body, or None on failure.
        """
        if not settings.vk_service_token:
            logging.error("VK_SERVICE_TOKEN is not set in .env")
            return None

        request_params = {
            "access_token": settings.vk_service_token,
            "v": "5.199",
            **params,
        }

        try:
            response = await self._api_client.get(
                f"{self._base_api_url}{method}",
                params=request_params,
            )
            data = response.json()

            if "error" in data:
                logging.error("VK API Error (%s): %s", method, data["error"])
                return None

            return data.get("response")
        except Exception as e:
            logging.exception("Failed to fetch VK API (%s): %s", method, e)
            return None

    async def _get_post_from_api(self, post_id: str) -> dict[str, Any] | None:
        """Fetch post data using wall.getById."""
        response_obj = await self._make_api_request(
            method="wall.getById",
            params={"posts": post_id},
        )
        if not response_obj:
            return None

        # VK API v5+ returns dict {"count": N, "items": [...]},
        # but sometimes list (rarely)
        if isinstance(response_obj, dict):
            items = response_obj.get("items", [])
        else:
            items = response_obj

        return items[0] if items else None

    async def _get_photos_from_api(self, photo_id: str) -> list[dict[str, Any]]:
        """Fetch photo data using photos.getById."""
        response_obj = await self._make_api_request(
            method="photos.getById",
            params={"photos": photo_id},
        )
        if not response_obj:
            return []

        if isinstance(response_obj, list):
            return response_obj
        return response_obj.get("items", [])

    async def _process_video_attachment(
        self,
        video_data: dict[str, Any],
    ) -> VideoDTO | None:
        """Process a single video attachment (Cache Check -> Download)."""
        owner_id = video_data.get("owner_id")
        vid = video_data.get("id")
        access_key = video_data.get("access_key")
        if not owner_id or not vid:
            return None

        source_id = f"{owner_id}_{vid}"
        video_url = f"https://vk.com/video{source_id}"
        if access_key:
            video_url += f"?list={access_key}"

        cached_item = await self._cache_dao.get_by_filters(
            source=SourceEnum.VK_VIDEO_YDL,
            source_id=source_id,
            quality="best",
            content_type=ContentTypeEnum.VIDEO,
        )

        if cached_item and isinstance(cached_item.meta_data_dto, VideoDTO):
            logging.info("Cache hit for video %s. Using file_id.", source_id)
            cached_dto = cached_item.meta_data_dto.model_copy()
            cached_dto.direct_download_url = cached_item.file_id
            cached_dto.path = None
            return cached_dto

        logging.info("Downloading video attachment: %s", video_url)
        try:
            info_dict = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=video_url,
                download=True,
            )

            file_id = info_dict.get("id")

            video_dto = VideoDTO.from_yt_dlp(
                info=info_dict,
                file_path=self._download_directory
                / f"{info_dict['id']}.{info_dict['ext']}",
                quality="best",
            )
            video_dto.source_id = source_id
            video_dto.thumbnail = self._get_thumbnail(file_id)
            return video_dto
        except DownloadError as e:
            error_msg = str(e)
            if (
                "Видео доступно только подписчикам" in error_msg
                or "Access restricted" in error_msg
                or "Private video" in error_msg
            ):
                logging.warning("Video privacy error: %s", error_msg)
                return None
            logging.error("Failed to download video %s: %s", video_url, e)
            return None
        except Exception:
            logging.exception("Unexpected error downloading video %s", video_url)
            return None

    async def _parse_wall_attachments(
        self,
        attachments: list[dict[str, Any]],
    ) -> tuple[list[PhotoDTO | VideoDTO], list[AudioDTO]]:
        """Parse post attachments into DTO lists."""
        media_group: list[PhotoDTO | VideoDTO] = []
        audio_list: list[AudioDTO] = []

        total_items = len(attachments)
        if total_items == 0:
            return media_group, audio_list

        for att in attachments:
            atype = att.get("type")

            if atype == "photo":
                photo_dto = PhotoDTO.from_vk_api(
                    photo_data=att.get("photo", {}),
                    resolution_url=self._resolution.url,
                )
                if photo_dto:
                    media_group.append(photo_dto)
            elif atype == "video":
                video_dto = await self._process_video_attachment(att.get("video", {}))
                if video_dto:
                    media_group.append(video_dto)
            elif atype == "audio":
                audio_dto = AudioDTO.from_vk_api(
                    audio_data=att.get("audio", {}),
                    resolution_url=self._resolution.url,
                )
                if audio_dto:
                    audio_list.append(audio_dto)

        return media_group, audio_list

    async def _send_wall_audio(self, audio_list: list[AudioDTO]) -> None:
        """Send audio files sequentially."""
        if not audio_list:
            return

        for i, audio in enumerate(audio_list):
            should_del = (i == len(audio_list) - 1) and (self._message_id is not None)
            msg_id_to_del = self._message_id if should_del else None

            await self._telegram_bot_controller.send_finish_downloading_audio(
                audio=audio,
                telegram_id=self._telegram_id,
                message_id=msg_id_to_del,
            )

    async def _handle_wall_post(self, code: str) -> None:
        """Handle downloading of a wall post."""
        post_data = await self._get_post_from_api(code)
        if not post_data:
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return

        attachments = list(post_data.get("attachments", []))
        if "copy_history" in post_data:
            for repost in post_data["copy_history"]:
                repost_attachments = repost.get("attachments", [])
                attachments.extend(repost_attachments)

        media_group, audio_list = await self._parse_wall_attachments(
            attachments,
        )
        if not media_group:
            await self._send_error_message()
            return

        self._process_percent(86)

        # 1. Send Visual Content
        await self._telegram_bot_controller.send_finish_downloading_group(
            files=media_group,
            telegram_id=self._telegram_id,
            message_id=self._message_id if not audio_list else None,
        )

        # 2. Send Audio
        await self._send_wall_audio(audio_list)

        # 3. Cleanup
        self.cleanup_files(media_group)

    async def _handle_single_photo(self, code: str) -> None:
        """Handle downloading of a direct photo link."""
        photos_data = await self._get_photos_from_api(code)
        if not photos_data:
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return

        photo_dto = PhotoDTO.from_vk_api(
            photo_data=photos_data[0],
            resolution_url=self._resolution.url,
        )
        if not photo_dto:
            await self._send_error_message()
            return

        self._process_percent(86)

        await self._telegram_bot_controller.send_finish_downloading_photo(
            photo=photo_dto,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
        )

        self.cleanup_files([photo_dto])

    async def download_video(self) -> None:
        """Main entry point: Routes to Wall or Photo logic based on metadata."""
        code = self._resolution.metadata.get("code")
        meta_type = self._resolution.metadata.get("type")
        if not code:
            await self._send_error_message()
            return

        self._process_percent(16)

        if meta_type == "photo":
            await self._handle_single_photo(code)
        else:
            # Default to wall post (type="wall")
            await self._handle_wall_post(code)
