import asyncio
import logging
from pathlib import Path
from typing import Any, ClassVar

import instaloader
from instaloader.exceptions import (
    PrivateProfileNotFollowedException,
    ProfileNotExistsException,
)
from instaloader.structures import Post, PostSidecarNode, Profile, StoryItem

from saver_backend.entities.enums import InstagramContentTypeEnum, SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import PhotoDTO, VideoDTO
from saver_backend.settings import settings


class InstagramInstaloaderController(BaseSourceController):
    """Controller for downloading Instagram posts and stories via Instaloader."""

    SOURCE: ClassVar[SourceEnum] = SourceEnum.INSTAGRAM_INSTALOADER
    COOKIES: ClassVar[bool] = True
    DIRECT_URL_DOWNLOAD: ClassVar[bool] = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)

        self.L = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            save_metadata=False,
            post_metadata_txt_pattern="",
            max_connection_attempts=3,
            fatal_status_codes=[400, 403, 429],
        )

    async def _try_login_with_session(self, login: str) -> bool:
        """Tries to load a session for a given username."""
        session_path = Path(f"cookies/{self.SOURCE.value}/{login}.session")
        if not session_path.exists():
            logging.error(
                "Instaloader session file not found for %s at %s",
                login,
                session_path,
            )
            return False

        try:
            self.L.load_session_from_file(username=login, filename=str(session_path))
            self._process_percent(percent=16)
            logging.info("Instaloader session successfully loaded for user %s", login)
            return True
        except Exception:
            logging.exception(
                "Failed to load Instaloader session for user %s",
                login,
            )
            return False

    def _get_post(self, source_id: str) -> Post:
        """Synchronously fetches Post metadata."""
        return Post.from_shortcode(self.L.context, source_id)

    def _get_story(self, source_id: int, username: str) -> StoryItem:
        """Synchronously fetches StoryItem metadata."""
        profile = Profile.from_username(self.L.context, username)
        for story in self.L.get_stories(userids=[profile.userid]):
            for item in story.get_items():
                if item.mediaid == source_id:
                    return item
        raise ValueError(f"Story with ID {source_id} not found.")

    async def _send_media(self, dtos: list[VideoDTO | PhotoDTO]) -> None:
        """Asynchronously sends DTOs to Telegram and caches them."""
        if not dtos:
            await self._send_error_message()
            return

        self._process_percent(percent=78)
        if len(dtos) == 1:
            item_dto = dtos[0]
            if isinstance(item_dto, VideoDTO):
                await self._send_video(item_dto)
            else:  # PhotoDTO
                await self._telegram_bot_controller.send_finish_downloading_photo(
                    photo=item_dto,
                    telegram_id=self._telegram_id,
                    message_id=self._message_id,
                )
                await self._create_history_entry()
        else:  # Media group
            await self._telegram_bot_controller.send_finish_downloading_group(
                files=dtos,
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )
            await self._create_history_entry()

    async def _get_post_dtos(self, source_id: str) -> list[VideoDTO | PhotoDTO]:
        """Fetches post metadata and converts it to DTOs."""
        post = await asyncio.to_thread(self._get_post, source_id)
        dtos: list[VideoDTO | PhotoDTO] = []

        nodes: list[Post | PostSidecarNode] = (
            [post]
            if post.typename != "GraphSidecar"
            else list(post.get_sidecar_nodes())
        )

        for node in nodes:
            if node.is_video:
                dtos.append(
                    VideoDTO.from_instaloader(
                        node,
                        self._resolution.url,
                        source_id=post.shortcode,
                        caption=post.caption,
                    ),
                )
            else:
                dtos.append(
                    PhotoDTO.from_instaloader(
                        node,
                        self._resolution.url,
                        source_id=post.shortcode,
                        caption=post.caption,
                    ),
                )
        return dtos

    async def _get_story_dtos(
        self,
        source_id: str,
        username: str,
    ) -> list[VideoDTO | PhotoDTO]:
        """Fetches story metadata and converts it to DTOs."""
        story_item = await asyncio.to_thread(
            self._get_story,
            int(source_id),
            username,
        )
        if story_item.is_video:
            return [
                VideoDTO.from_instaloader(story_item, self._resolution.url, source_id),
            ]
        return [PhotoDTO.from_instaloader(story_item, self._resolution.url, source_id)]

    async def download_video(self) -> None:
        """Asynchronously downloads content from Instagram."""
        source_id = self._resolution.metadata.get("code")

        if not source_id:
            logging.error("No 'source_id' found in resolution metadata.")
            await self._send_error_message()
            return

        is_sent_from_cache = await self.send_video_from_cache(
            source_id=source_id,
            quality="best",
        )
        if is_sent_from_cache:
            return

        login = settings.instagram_account.split(":")[0]
        is_logged_in = await self._try_login_with_session(login)
        if not is_logged_in:
            logging.error("Instaloader failed to log in with any available session.")
            await self._send_error_message()
            return

        content_type = self._resolution.metadata.get("type")
        username = self._resolution.metadata.get("user")
        dtos: list[VideoDTO | PhotoDTO] = []

        try:
            content_type = InstagramContentTypeEnum(content_type)
        except ValueError:
            logging.error(
                "Invalid content type received from resolver: %s",
                content_type,
            )
            await self._send_error_message()
            return

        try:
            if content_type == InstagramContentTypeEnum.POST:
                dtos = await self._get_post_dtos(source_id)
            elif content_type == InstagramContentTypeEnum.STORIES:
                if not username:
                    raise ValueError("Username is required for stories.")
                dtos = await self._get_story_dtos(source_id, username)

            await self._send_media(dtos)
        except (
            ProfileNotExistsException,
            PrivateProfileNotFollowedException,
            ValueError,
        ) as e:
            logging.warning("Instaloader could not access content %s: %s", source_id, e)
            await self._send_content_not_found_error()
        except Exception:
            logging.exception(
                "An unexpected error occurred during Instaloader download for %s",
                source_id,
            )
            await self._send_error_message()

    async def _send_content_not_found_error(self) -> None:
        """Send content not found/private error message."""
        if self._message_id:
            await self._telegram_bot_controller.delete_message(
                telegram_id=self._telegram_id,
                message_id=self._message_id,
            )
        await self._telegram_bot_controller.send_content_not_found_error(
            telegram_id=self._telegram_id,
        )

    async def close(self) -> None:
        """Close Instaloader session."""
        self.L.close()
