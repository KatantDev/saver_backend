import asyncio
import logging
import time
from pathlib import Path
from typing import Any, ClassVar

from httpx import AsyncClient, RequestError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from saver_backend.entities.enums import ProxyType, SourceEnum
from saver_backend.services.consts import BASE_DOWNLOAD_PATH
from saver_backend.services.downloaders.base_source import BaseSourceController
from saver_backend.services.downloaders.schema import (
    PhotoListDTO,
    TikWMData,
    TikWMResponse,
    VideoDTO,
)
from saver_backend.services.downloaders.tiktok_ydl_source import TikTokYdlController
from saver_backend.services.i18n import gettext as _
from saver_backend.settings import settings

# Free tikwm.com allows one request per second for the whole server,
# so all workers take turns through a shared slot in Redis.
TIKWM_SLOT_KEY = "tikwm:slot"
TIKWM_INTERVAL_MS = 1100
TIKWM_MAX_WAIT = 60
TIKWM_ATTEMPTS = 3


class TikTokAPIController(BaseSourceController):
    """Controller for downloading videos from TikTok via tikwm.com API."""

    SOURCE = SourceEnum.TIKTOK
    PROXY_TYPE: ClassVar[ProxyType] = ProxyType.LOCAL
    DIRECT_URL_DOWNLOAD = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._download_directory = BASE_DOWNLOAD_PATH / self.SOURCE.value
        self._download_directory.mkdir(parents=True, exist_ok=True)
        self._temp_files: list[Path] = []
        self._video: VideoDTO | None = None

        self._api_url = "https://www.tikwm.com/api/"
        self._client = AsyncClient(proxy=self._proxy)
        self._redis = Redis.from_url(str(settings.redis_url))

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
        await self._redis.aclose()

    async def _wait_for_slot(self) -> None:
        """Wait until it is this request's turn to call tikwm.com."""
        deadline = time.monotonic() + TIKWM_MAX_WAIT
        try:
            while time.monotonic() < deadline:
                if await self._redis.set(
                    TIKWM_SLOT_KEY,
                    1,
                    nx=True,
                    px=TIKWM_INTERVAL_MS,
                ):
                    return
                wait_ms = await self._redis.pttl(TIKWM_SLOT_KEY)
                await asyncio.sleep(max(wait_ms, 50) / 1000)
        except RedisError:
            logging.warning("Redis is unavailable, calling tikwm without a queue")
            return
        logging.warning("Waited %ss for a tikwm slot, calling anyway", TIKWM_MAX_WAIT)

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Fetch video info from TikWM API.

        This method acts as the info-gathering step for TikTok,
        returning the raw API response data for further processing.

        :param url: The TikTok video URL.
        :return: The 'data' part of the TikWM API response or None on failure.
        """
        for _attempt in range(TIKWM_ATTEMPTS):
            await self._wait_for_slot()
            try:
                response = await self._client.post(
                    self._api_url,
                    data={"url": url},
                    timeout=30,
                )
                response.raise_for_status()
                api_response = TikWMResponse.model_validate(response.json())
            except RequestError as e:
                logging.exception(e)
                return None

            if api_response.code == 0 and api_response.data:
                return api_response.data.model_dump()

            logging.error(
                "TikWM API returned an error: %s (URL: %s)",
                api_response.msg,
                self._resolution.url,
            )
            if "limit" not in api_response.msg.lower():
                return None
        return None

    async def _fallback_to_ydl(self) -> None:
        """Retry the download through yt-dlp when tikwm.com fails."""
        logging.warning(
            "tikwm failed, falling back to yt-dlp: %s; user_id=%s",
            self._resolution.url,
            self._telegram_id,
        )
        controller = TikTokYdlController(
            resolution=self._resolution,
            telegram_bot_controller=self._telegram_bot_controller,
            telegram_id=self._telegram_id,
            user_dao=self._user_dao,
            history_dao=self._history_dao,
            cache_dao=self._cache_dao,
            message_id=self._message_id,
            inline_query_id=self._inline_query_id,
        )
        try:
            await controller.download_video()
        except Exception:
            logging.exception("yt-dlp fallback failed: %s", self._resolution.url)
            self._message_id = controller.message_id
            await self._send_error_message()
        finally:
            await controller.close()

    async def _handle_slideshow(
        self,
        data: TikWMData,
    ) -> None:
        """
        Handle downloading and sending of a photo slideshow with audio.

        :param data: The data to send.
        """
        if self._inline_query_id:
            await self._telegram_bot_controller.answer_inline_query_error(
                inline_query_id=self._inline_query_id,
                error_text=_("tiktok photo unsupported in inline query"),
            )
            return

        if not data.images:
            await self._send_error_message()
            return

        slideshow_dto = PhotoListDTO.from_tikwm(
            data=data,
            resolution_url=self._resolution.url,
        )

        await self._telegram_bot_controller.send_finish_downloading_group(
            files=slideshow_dto.photos,
            telegram_id=self._telegram_id,
            message_id=self._message_id,
        )

        if slideshow_dto.audio:
            await self._telegram_bot_controller.send_finish_downloading_audio(
                audio=slideshow_dto.audio,
                telegram_id=self._telegram_id,
            )

        await self._create_history_entry()

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
        url_code = str(self._resolution.metadata.get("code"))
        if await self.send_video_from_cache(
            source_id=url_code,
            quality="default",
        ):
            return

        info = await self.get_video_info(url=self._resolution.url)
        if not info:
            await self._fallback_to_ydl()
            return

        data = TikWMData.model_validate(info)

        if data.images and data.music:
            await self._handle_slideshow(data)
        elif data.play and data.cover:
            await self._handle_video(data)
        else:
            await self._send_error_message()
