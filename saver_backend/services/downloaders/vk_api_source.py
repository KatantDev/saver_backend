import asyncio
import logging
import secrets
from typing import Any, ClassVar, List, Tuple, Union

from vkbottle import API, VKAPIError
from vkbottle.http import AiohttpClient
from vkbottle_types.objects import (
    PhotosPhoto,
    VideoVideo,
    WallWallpostAttachment,
    WallWallpostAttachmentType,
    WallWallpostFull,
)
from yt_dlp.utils import DownloadError

from saver_backend.entities.enums import ContentTypeEnum, SourceEnum
from saver_backend.services.downloaders.schema import AudioDTO, PhotoDTO, VideoDTO
from saver_backend.services.downloaders.ydl_source import YtDlpController
from saver_backend.settings import settings


class VKAPIController(YtDlpController):
    """
    Unified controller for VK Wall posts and Photos via VK API (using vkbottle).

    Handles:
    - vk.com/wall-XXXX_YYYY (Posts with mixed content)
    - vk.com/photo-XXXX_YYYY (Direct photos)
    """

    SOURCE: ClassVar[SourceEnum] = SourceEnum.VK_API_YDL
    COOKIES: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        if not settings.vk_service_token:
            logging.error("VK_SERVICE_TOKEN is not set in .env")
        vk_service_token = secrets.choice(settings.vk_service_token)

        self._api = API(
            token=vk_service_token,
            http_client=AiohttpClient(),
        )

        vk_params = {
            "downloader": "aria2c",
            "downloader_args": ["-x", "16", "-s", "16", "-k", "1M"],
            "quiet": True,
            "noprogress": True,
        }
        self._yt_dlp.params.update(vk_params)

    async def close(self) -> None:
        """Close the VK API client and the superclass resources."""
        if self._api:
            await self._api.http_client.close()
        await super().close()

    async def _get_post_from_api(self, post_id: str) -> WallWallpostFull | None:
        """
        Fetch post data using wall.getById via vkbottle.

        Returns a typed WallWallpostFull object.
        """
        try:
            response = await self._api.wall.get_by_id(posts=[post_id])
            if not response or not response.items:
                logging.warning("VK API returned no items for post %s", post_id)
                return None

            return response.items[0]
        except VKAPIError as e:
            logging.error(
                "VK API Error (wall.getById): code=%s, msg=%s",
                e.code,
                e.error_msg,
            )
            return None
        except Exception as e:
            logging.exception("Failed to fetch VK API (wall.getById): %s", e)
            return None

    async def _get_photos_from_api(self, photo_id: str) -> List[PhotosPhoto]:
        """Fetch photo data using photos.getById via vkbottle."""
        try:
            response = await self._api.photos.get_by_id(photos=[photo_id])
            if not response:
                return []

            return response
        except VKAPIError as e:
            logging.error(
                "VK API Error (photos.getById): code=%s, msg=%s",
                e.code,
                e.error_msg,
            )
            return []
        except Exception as e:
            logging.exception("Failed to fetch VK API (photos.getById): %s", e)
            return []

    async def _process_video_attachment(
        self,
        video: VideoVideo,
    ) -> VideoDTO | None:
        """Process a single video attachment (Cache Check -> Download)."""
        if not video.owner_id or not video.id:
            return None

        source_id = f"{video.owner_id}_{video.id}"
        video_url = f"https://vk.com/video{source_id}"
        if video.access_key:
            video_url += f"?list={video.access_key}"

        # 1. Check Cache
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

        # 2. Download Info via yt-dlp
        logging.info("Downloading video attachment info: %s", video_url)
        try:
            info_dict = await asyncio.to_thread(
                self._yt_dlp.extract_info,
                url=video_url,
                download=True,
            )

            file_id = info_dict.get("id")
            predicted_path = (
                self._download_directory / f"{file_id}.{info_dict.get('ext', 'mp4')}"
            )
            video_dto = VideoDTO.from_yt_dlp(
                info=info_dict,
                file_path=predicted_path,
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
        attachments: List[WallWallpostAttachment],
    ) -> Tuple[List[Union[PhotoDTO, VideoDTO]], List[AudioDTO]]:
        """Parse post attachments using vkbottle types."""
        media_group: List[Union[PhotoDTO, VideoDTO]] = []
        audio_list: List[AudioDTO] = []
        if not attachments:
            return media_group, audio_list

        for att in attachments:
            if att.type == WallWallpostAttachmentType.PHOTO and att.photo:
                photo_dto = PhotoDTO.from_vk_api(
                    photo_data=att.photo.model_dump(),
                    resolution_url=self._resolution.url,
                )
                if photo_dto:
                    media_group.append(photo_dto)
            elif att.type == WallWallpostAttachmentType.VIDEO and att.video:
                video_dto = await self._process_video_attachment(att.video)
                if video_dto:
                    media_group.append(video_dto)
            elif att.type == WallWallpostAttachmentType.AUDIO and att.audio:
                audio_dto = AudioDTO.from_vk_api(
                    audio_data=att.audio.model_dump(),
                    resolution_url=self._resolution.url,
                )
                if audio_dto:
                    audio_list.append(audio_dto)

        return media_group, audio_list

    async def _send_wall_audio(self, audio_list: List[AudioDTO]) -> None:
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
        # 1. Fetch Post Data (Typed Object)
        post = await self._get_post_from_api(code)
        if not post:
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return

        logging.info("Processing post ID: %s", post.id)

        # 2. Collect Attachments (including reposts)
        attachments: List[WallWallpostAttachment] = post.attachments or []

        if post.copy_history:
            for repost in post.copy_history:
                if repost.attachments:
                    attachments.extend(repost.attachments)

        # 3. Parse Attachments into DTOs
        media_group, audio_list = await self._parse_wall_attachments(attachments)
        if not media_group and not audio_list:
            await self._send_error_message()
            return

        self._process_percent(86)

        # 4. Send Visual Content (Photos/Videos)
        if media_group:
            msg_id = self._message_id if not audio_list else None
            await self._telegram_bot_controller.send_finish_downloading_group(
                files=media_group,
                telegram_id=self._telegram_id,
                message_id=msg_id,
            )

        # 5. Send Audio
        await self._send_wall_audio(audio_list)

        # 6. Cleanup
        self.cleanup_files(media_group)

    async def _handle_single_photo(self, code: str) -> None:
        """Handle downloading of a direct photo link."""
        photos = await self._get_photos_from_api(code)
        if not photos:
            await self.delete_processing_message()
            await self._telegram_bot_controller.send_content_not_found_error(
                telegram_id=self._telegram_id,
            )
            return

        logging.info("Processing photo ID: %s", photos[0].id)

        photo_dto = PhotoDTO.from_vk_api(
            photo_data=photos[0].model_dump(),
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
