import logging
from pathlib import Path
from typing import Any

from httpx import AsyncClient, RequestError

from saver_backend.entities.enums import SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import (
    AudioDTO,
    PhotoDTO,
    TikWMData,
    TikWMResponse,
    VideoDTO,
)


class TikTokAPIController(BaseSourceController):
    """Controller for downloading videos from TikTok via tikwm.com API."""

    SOURCE = SourceEnum.TIKTOK
    DIRECT_URL_DOWNLOAD = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)
        self._temp_files: list[Path] = []

        self._api_url = "https://www.tikwm.com/api/"
        self._client = AsyncClient()

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Fetch video info from TikWM API.

        This method acts as the info-gathering step for TikTok,
        returning the raw API response data for further processing.

        :param url: The TikTok video URL.
        :return: The 'data' part of the TikWM API response or None on failure.
        """
        try:
            response = await self._client.post(
                self._api_url,
                data={"url": url},
                timeout=30,
            )
            response.raise_for_status()
            api_response = TikWMResponse.model_validate(response.json())

            if api_response.code == 0 and api_response.data:
                return api_response.data.model_dump()

            logging.error(
                "TikWM API returned an error: %s (URL: %s)",
                api_response.msg,
                self._resolution.url,
            )
            return None
        except RequestError as e:
            logging.error("Request to TikWM API failed: %s", e)
            return None

    async def get_video_dto(self) -> VideoDTO | None:
        """
        Fetch video info from TikWM API and wrap it in a VideoDTO.

        Handles slideshows by returning a DTO without a direct video URL.

        :return: A VideoDTO instance or None on failure.
        """
        info = await self.get_video_info(url=self._resolution.url)
        if not info:
            return None

        tikwm_data = TikWMData.model_validate(info)

        # For slideshows, we create a DTO that signals it's not a video.
        if tikwm_data.images:
            return VideoDTO(
                source_id=tikwm_data.id,
                url=self._resolution.url,
                title=tikwm_data.title,
                formats=[],  # Empty formats indicate it's not a standard video
            )

        return VideoDTO.from_tikwm(data=tikwm_data, url=self._resolution.url)

    async def _handle_slideshow(
        self,
        data: TikWMData,
    ) -> None:
        """
        Handle downloading and sending of a photo slideshow with audio.

        :param data: The data to send.
        """
        if not data.images or not data.music:
            await self._send_error_message()
            return

        photos = [
            PhotoDTO.from_tikwm(
                image_url=img_url,
                data=data,
                resolution_url=self._resolution.url,
            )
            for img_url in data.images
        ]

        await self._telegram_bot_controller.send_finish_downloading_group(
            files=photos,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
        )

        # Create audio DTO and send it separately
        audio = AudioDTO.from_tikwm(data=data, resolution_url=self._resolution.url)
        if audio:
            await self._telegram_bot_controller.send_finish_downloading_audio(
                audio=audio,
                telegram_id=self._telegram_id,
            )

    async def _handle_video(self, data: TikWMData) -> None:
        """
        Handle caching, downloading, and sending of a single video.

        :param data: The data to send.
        """
        if not data.play or not data.cover:
            await self._send_error_message()
            return

        quality = "default"

        is_sent_from_cache = await self.send_video_from_cache(
            source_id=data.id,
            quality=quality,
        )
        if is_sent_from_cache:
            return

        video_dto = VideoDTO.from_tikwm(
            data=data,
            url=self._resolution.url,
            quality=quality,
        )
        await self._send_video(video_dto)

    async def download_video(self) -> None:
        """Download video or photo set from TikTok using tikwm.com API."""
        if self._inline_query_id:
            await self._handle_inline_query()
            return

        info = await self.get_video_info(url=self._resolution.url)
        if not info:
            await self._send_error_message()
            return

        data = TikWMData.model_validate(info)

        if data.images and data.music:
            await self._handle_slideshow(data)
        elif data.play and data.cover:
            await self._handle_video(data)
        else:
            await self._send_error_message()
