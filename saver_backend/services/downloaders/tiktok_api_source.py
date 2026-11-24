import asyncio
import logging
import time
from pathlib import Path
from typing import Any, ClassVar

from httpx import AsyncClient, HTTPError
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
# tikwm.com answers in about a second. On 23.11 it answered in 20 seconds or
# with «502 Bad Gateway» for five hours: every TikTok video took 20 seconds,
# 39 requests failed and inline mode stopped working, while yt-dlp, which reads
# TikTok itself, was never asked. Now a request that is not answered in time or
# fails goes to yt-dlp, and after two such requests in a row tikwm.com is left
# out for a few minutes.
TIKWM_TIMEOUT = 8
TIKWM_FAILS_KEY = "tikwm:fails"
TIKWM_FAILS_WINDOW = 120
TIKWM_FAILS_TO_SKIP = 2
TIKWM_DOWN_KEY = "tikwm:down"
TIKWM_SKIP_SECONDS = 180


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

    async def _tikwm_is_down(self) -> bool:
        """Whether tikwm.com failed recently and is left out for now."""
        try:
            return bool(await self._redis.exists(TIKWM_DOWN_KEY))
        except RedisError:
            return False

    async def _note_tikwm_failure(self) -> None:
        """Count a failed request; after two in a row leave tikwm.com out."""
        try:
            fails = await self._redis.incr(TIKWM_FAILS_KEY)
            if fails == 1:
                await self._redis.expire(TIKWM_FAILS_KEY, TIKWM_FAILS_WINDOW)
            if fails >= TIKWM_FAILS_TO_SKIP:
                await self._redis.set(TIKWM_DOWN_KEY, 1, ex=TIKWM_SKIP_SECONDS)
                logging.warning(
                    "tikwm failed %s times in a row, using yt-dlp for %s seconds",
                    fails,
                    TIKWM_SKIP_SECONDS,
                )
        except RedisError:
            return

    async def _note_tikwm_answer(self) -> None:
        """Forget earlier failures once tikwm.com has answered in time."""
        try:
            await self._redis.delete(TIKWM_FAILS_KEY)
        except RedisError:
            return

    async def get_video_info(self, url: str) -> dict[str, Any] | None:
        """
        Fetch video info from TikWM API.

        This method acts as the info-gathering step for TikTok,
        returning the raw API response data for further processing.

        :param url: The TikTok video URL.
        :return: The 'data' part of the TikWM API response or None on failure.
        """
        if await self._tikwm_is_down():
            logging.warning("tikwm is left out for now, going to yt-dlp: %s", url)
            return None

        for _attempt in range(TIKWM_ATTEMPTS):
            await self._wait_for_slot()
            try:
                response = await self._client.post(
                    self._api_url,
                    data={"url": url},
                    timeout=TIKWM_TIMEOUT,
                )
                response.raise_for_status()
                api_response = TikWMResponse.model_validate(response.json())
            except (HTTPError, ValueError) as e:
                # No answer in time, a refused connection, a 5xx or a broken body
                logging.warning(
                    "tikwm request failed (%s: %s): %s",
                    type(e).__name__,
                    e,
                    url,
                )
                await self._note_tikwm_failure()
                return None

            await self._note_tikwm_answer()
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
